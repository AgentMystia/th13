"""按地址直调原版函数的命令行探针。

用法：
    python3 -m oracle.call 0x44b9e0 [arg1 arg2 ...]   # 十六进制参数
    python3 -m oracle.call 0x44b9e0 --str 1 "seed"    # TODO: 参数类型扩展
"""
import sys

from .machine import Machine

EXE = "games/th13/th13.exe"


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    address = int(argv[0], 16)
    args = [int(a, 0) for a in argv[1:]]
    m = Machine(EXE)
    print(f"call 0x{address:08x} args={[hex(a) for a in args]}")
    eax = m.call(address, args=args)
    print(f"eax = 0x{eax:08x} ({eax})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
