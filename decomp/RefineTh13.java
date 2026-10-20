// RefineTh13.java — 反编译质量复核脚本（第二轮）
// 1) 对含 decompiler artifact（unaff_/in_E…）或首轮失败的函数，以更长超时与
//    寄存器参数提示重试，覆盖 functions/ 下对应 .c；
// 2) 输出 refine-report.json，列出仍需人工签名修正的函数清单。
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class RefineTh13 extends GhidraScript {

    private static void writeFile(File f, String content) throws Exception {
        try (OutputStreamWriter w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8)) {
            w.write(content);
        }
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: RefineTh13.java <outputDir>");
            return;
        }
        File outDir = new File(args[0]);
        File funcDir = new File(outDir, "functions");
        if (!funcDir.isDirectory()) {
            println("no functions dir; run ExportTh13.java first");
            return;
        }

        DecompInterface ifc = new DecompInterface();
        DecompileOptions options = new DecompileOptions();
        options.setMaxWidth(120);
        ifc.setOptions(options);
        ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);

        List<Map<String, Object>> report = new ArrayList<>();
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        while (it.hasNext()) {
            if (monitor.isCancelled()) break;
            Function f = it.next();
            String addr = f.getEntryPoint().toString();
            File c = new File(funcDir, addr + ".c");
            String prev = c.isFile() ? new String(java.nio.file.Files.readAllBytes(c.toPath()), StandardCharsets.UTF_8) : "";
            boolean needsWork = prev.isEmpty()
                    || prev.contains("unaff_") || prev.contains("in_E") || prev.contains("extraout_");
            if (!needsWork) continue;

            DecompileResults res = ifc.decompileFunction(f, 180, monitor);
            Map<String, Object> rec = new LinkedHashMap<>();
            rec.put("address", addr);
            rec.put("name", f.getName());
            if (res != null && res.getDecompiledFunction() != null) {
                String body = res.getDecompiledFunction().getC();
                boolean stillArtifact = body.contains("unaff_") || body.contains("in_E") || body.contains("extraout_");
                writeFile(c, String.format("// %s %s%n", addr, f.getName()) + body);
                rec.put("status", stillArtifact ? "artifact-remains" : "recovered");
            } else {
                rec.put("status", "decompile-failed");
            }
            report.add(rec);
        }
        ifc.dispose();

        StringBuilder sb = new StringBuilder("{\"refined\":[\n");
        for (Map<String, Object> rec : report) {
            sb.append(String.format("  {\"address\":\"%s\",\"name\":\"%s\",\"status\":\"%s\"},%n",
                    rec.get("address"), rec.get("name"), rec.get("status")));
        }
        sb.append("]}\n");
        writeFile(new File(outDir, "refine-report.json"), sb.toString());
        println("refine complete: " + report.size() + " functions reprocessed");
    }
}
