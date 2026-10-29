"""Passive diagnosis of original AsciiInf text queue; no draw hooks changed."""
import json
import sys
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ESP, UC_X86_REG_EBP
from oracle.battle_oracle import BattleOracle, main

install_hooks = BattleOracle.install_hooks
def traced_hooks(self):
    install_hooks(self)
    output = open(self.diagnostic_dir + "/text-probe.jsonl", "w")
    def hook(uc, address, size, user_data):
        source = uc.reg_read(UC_X86_REG_EAX)
        text = bytes(self.cstr(source, 256))
        if any(byte < 0x20 and byte != 10 or byte > 0x7e for byte in text):
            esp = uc.reg_read(UC_X86_REG_ESP)
            output.write(json.dumps({"frame": self.frame, "source": source,
                "text_hex": text.hex(), "stack": [self.rd32(esp + 4 * i)
                for i in range(12)], "format_hex": bytes(self.cstr(
                    self.rd32(uc.reg_read(UC_X86_REG_EBP) + 8))).hex(),
                "format_caller": self.rd32(uc.reg_read(UC_X86_REG_EBP) + 4),
                "args_hex": bytes(uc.mem_read(uc.reg_read(UC_X86_REG_EBP) + 12, 32)).hex()}, separators=(",", ":")) + "\n")
            output.flush()
    self.uc.hook_add(UC_HOOK_CODE, hook, begin=0x00403DA0, end=0x00403DA0)
BattleOracle.install_hooks = traced_hooks
sys.exit(main(sys.argv))
