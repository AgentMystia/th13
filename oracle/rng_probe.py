"""RNG 级 oracle 闭环探针。

1. 在 Ghidra 导出的 functions.json 中定位包含 MSVC LCG 常量对（0x47BBAE）的函数
   —— 即静态链接的 CRT rand()；
2. Unicorn 直调该函数 8 次，输出原版序列；
3. 与 WASI harness（th13::Rng, MSVC 常量镜像）输出比对。
"""
import json

from .machine import Machine

CONST_SITE = 0x47BBAE  # th13.exe 内唯一一处 0x343FD 立即数地址
EXE = "games/th13/th13.exe"
FUNCS = "artifacts/decomp/jp/functions.json"


def find_function_containing(functions, addr):
    for f in functions:
        start = int(f["address"], 16)
        if start <= addr < start + f["bodyBytes"]:
            return f
    return None


def main():
    functions = json.load(open(FUNCS))["functions"]
    rand_f = find_function_containing(functions, CONST_SITE)
    if rand_f is None:
        raise SystemExit(f"no function contains 0x{CONST_SITE:x}; rerun Ghidra export")
    rand_addr = int(rand_f["address"], 16)
    print(f"rand() = {rand_f['address']} ({rand_f['name']}), callers={len(rand_f['callers'])}")
    for c in rand_f["callers"][:12]:
        caller = find_function_containing(functions, int(c, 16))
        if caller:
            print(f"  caller {c} ({caller['name']})")

    m = Machine(EXE)
    # 0x47fe5a = MSVC CRT _getptd()：返回线程数据块指针，rand 的状态在 ptd+0x14。
    # 无头环境下以伪造 ptd 覆写，等价于 srand(1)。
    ptd = m.malloc(0x200)
    m.write_u32(ptd + 0x14, 1)
    m.override_call(0x47FE5A, lambda machine: ptd)
    rand_addr = int(rand_f["address"], 16)
    seq = [m.call(rand_addr) & 0x7FFF for _ in range(8)]
    print("oracle rand sequence:", seq)
    print("expected (MSVC srand(1)):", [41, 18467, 6334, 26500, 19169, 15724, 11478, 29358])
    return seq


if __name__ == "__main__":
    main()
