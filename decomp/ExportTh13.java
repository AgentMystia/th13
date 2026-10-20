// ExportTh13.java — Ghidra headless 后置脚本
// 导出 th13.exe 全量反编译候选：函数体 C、调用图、内存块、导入表、字符串。
// 产物为开发期对照证据，不进入游戏构建。
// 用法：analyzeHeadless <proj> <name> -import th13.exe \
//         -scriptPath decomp -postScript ExportTh13.java <outputDir>
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;
import ghidra.program.model.symbol.SymbolType;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class ExportTh13 extends GhidraScript {

    private static String jsonEscape(String s) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20 || c > 0x7e) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        return b.toString();
    }

    private static void writeFile(File f, String content) throws Exception {
        try (OutputStreamWriter w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8)) {
            w.write(content);
        }
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: ExportTh13.java <outputDir>");
            return;
        }
        File outDir = new File(args[0]);
        File funcDir = new File(outDir, "functions");
        funcDir.mkdirs();
        println("exporting to " + outDir.getAbsolutePath());

        // ---- 内存块 ----
        StringBuilder blocks = new StringBuilder("[\n");
        for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
            blocks.append(String.format("{\"name\":\"%s\",\"start\":\"%s\",\"size\":%d,\"type\":\"%s\",\"initialized\":%s},%n",
                    jsonEscape(block.getName()), block.getStart(), block.getSize(),
                    jsonEscape(String.valueOf(block.getType())), block.isInitialized()));
        }
        blocks.append("]\n");
        writeFile(new File(outDir, "blocks.json"), blocks.toString());

        // ---- 导入（外部符号）----
        SymbolTable st = currentProgram.getSymbolTable();
        StringBuilder imports = new StringBuilder("[\n");
        List<String> importNames = new ArrayList<>();
        for (Symbol sym : st.getExternalSymbols()) {
            importNames.add(sym.getName(true));
            imports.append(String.format("{\"name\":\"%s\"},%n", jsonEscape(sym.getName(true))));
        }
        imports.append("]\n");
        writeFile(new File(outDir, "imports.json"), imports.toString());
        println("external symbols: " + importNames.size());

        // ---- 已定义字符串（定位版本号/调试名）----
        DataIterator dataIt = currentProgram.getListing().getDefinedData(true);
        StringBuilder strings = new StringBuilder("[\n");
        long stringCount = 0;
        while (dataIt.hasNext()) {
            Data d = dataIt.hasNext() ? dataIt.next() : null;
            if (d == null) break;
            Object value = d.getValue();
            if (value instanceof String) {
                String s = (String) value;
                if (s.length() >= 4) {
                    strings.append(String.format("{\"address\":\"%s\",\"value\":\"%s\"},%n",
                            d.getAddress(), jsonEscape(s)));
                    stringCount++;
                }
            }
        }
        strings.append("]\n");
        writeFile(new File(outDir, "strings.json"), strings.toString());
        println("strings: " + stringCount);

        // ---- 反编译 + 调用图 ----
        DecompInterface ifc = new DecompInterface();
        DecompileOptions options = new DecompileOptions();
        options.setMaxWidth(120);
        ifc.setOptions(options);
        ifc.openProgram(currentProgram);

        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        List<Map<String, Object>> records = new ArrayList<>();
        long total = currentProgram.getFunctionManager().getFunctionCount();
        println("functions: " + total);
        long done = 0;
        while (it.hasNext()) {
            if (monitor.isCancelled()) break;
            Function f = it.next();
            done++;
            if (done % 100 == 0) {
                println("decompiling " + done + "/" + total);
                monitor.setProgress((int) (done * 100 / Math.max(total, 1)));
            }

            Map<String, Object> rec = new LinkedHashMap<>();
            String addr = f.getEntryPoint().toString();
            rec.put("address", addr);
            rec.put("name", f.getName());
            rec.put("isThunk", f.isThunk());
            rec.put("callingConvention", f.getCallingConventionName());
            rec.put("parameterCount", f.getParameterCount());
            rec.put("bodyBytes", f.getBody().getNumAddresses());

            // 被调用者：函数体内 CALL 引用
            List<String> callees = new ArrayList<>();
            InstructionIterator iit = currentProgram.getListing().getInstructions(f.getBody(), true);
            while (iit.hasNext()) {
                Instruction instr = iit.next();
                for (Reference ref : instr.getReferencesFrom()) {
                    if (ref.getReferenceType().isCall()) {
                        Function callee = currentProgram.getFunctionManager().getFunctionAt(ref.getToAddress());
                        if (callee != null) callees.add(callee.getEntryPoint().toString());
                    }
                }
            }
            rec.put("calls", callees);

            // 调用者：入口点被引用次数（去重）
            List<String> callers = new ArrayList<>();
            ReferenceIterator rit = currentProgram.getReferenceManager().getReferencesTo(f.getEntryPoint());
            while (rit.hasNext()) {
                Reference ref = rit.next();
                if (ref.getReferenceType().isCall()) {
                    Function caller = currentProgram.getFunctionManager().getFunctionContaining(ref.getFromAddress());
                    if (caller != null && !callers.contains(caller.getEntryPoint().toString())) {
                        callers.add(caller.getEntryPoint().toString());
                    }
                }
            }
            rec.put("callers", callers);

            // 反编译体
            DecompileResults res = ifc.decompileFunction(f, 45, monitor);
            String c = null;
            boolean artifact = false;
            if (res != null && res.getDecompiledFunction() != null) {
                c = res.getDecompiledFunction().getC();
                artifact = c.contains("unaff_") || c.contains("in_E") || c.contains("in_other_") || c.contains("extraout_");
            }
            rec.put("decompiled", c != null);
            rec.put("decompilerArtifact", artifact);
            records.add(rec);

            if (c != null) {
                String header = String.format("// %s %s%n", addr, f.getName());
                writeFile(new File(funcDir, addr + ".c"), header + c);
            }
        }
        ifc.dispose();

        StringBuilder functions = new StringBuilder("{\"functions\":[\n");
        for (Map<String, Object> rec : records) {
            functions.append("  {");
            boolean first = true;
            for (Map.Entry<String, Object> e : rec.entrySet()) {
                if (!first) functions.append(",");
                first = false;
                Object v = e.getValue();
                if (v instanceof String) functions.append(String.format("\"%s\":\"%s\"", e.getKey(), jsonEscape((String) v)));
                else if (v instanceof Boolean) functions.append(String.format("\"%s\":%s", e.getKey(), v));
                else if (v instanceof Number) functions.append(String.format("\"%s\":%d", e.getKey(), ((Number) v).longValue()));
                else if (v instanceof List) {
                    StringBuilder arr = new StringBuilder("[");
                    boolean f2 = true;
                    for (Object o : (List<?>) v) {
                        if (!f2) arr.append(",");
                        f2 = false;
                        arr.append("\"").append(jsonEscape(String.valueOf(o))).append("\"");
                    }
                    arr.append("]");
                    functions.append(String.format("\"%s\":%s", e.getKey(), arr));
                }
            }
            functions.append("},\n");
        }
        functions.append("]}\n");
        writeFile(new File(outDir, "functions.json"), functions.toString());
        println("export complete: " + records.size() + " functions, artifacts flagged");
    }
}
