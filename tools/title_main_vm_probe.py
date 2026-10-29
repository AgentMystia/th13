#!/usr/bin/env python3
"""Read-only original title ANM VM time series; no synthetic VM inputs."""
import json, struct, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oracle.title_oracle import TitleOracle, TITLE_OBJ, VK_DOWN, VK_Z
out=Path('artifacts/th13-longrun-20261029/title-main-vm-sequence')
out.mkdir(parents=True,exist_ok=True)
class Probe(TitleOracle):
 def title_frame(self,log):
  super().title_frame(log)
  t=self.rd32(TITLE_OBJ)
  if not t:return
  state=[self.rd32(t+0x1c),self.rd32(t+0x24),self.rd32(t+0x28)]
  if not hasattr(self,'rows'): self.rows=[]; self.first=None
  if state[:2]==[1,2] and self.first is None:
   self.first=self.frame; self.input_script[self.frame+20]=frozenset({VK_DOWN}); self.input_script[self.frame+60]=frozenset({VK_Z})
  if self.frame<350 or (self.first is not None and self.frame>self.first+90):return
  vms=[]
  for layer in range(33):
   v=self.rd32(self.anm_mgr+0xf48784+layer*0x5a4);n=0
   while v and n<200:
    try:
     script=self._vm_script(v)
     if 3<=script<=22 or 89<=script<=100:
      u16=lambda a:struct.unpack('<H',self.uc.mem_read(a,2))[0]
      f=lambda off:struct.unpack('<f',self.uc.mem_read(v+off,4))[0]
      vms.append(dict(script=script,sprite=u16(v+0x34),color=self.rd32(v+0x528),flags=self.rd32(v+0x18),render_flags=self.rd32(v+0x1c),position=[f(0x3c),f(0x40)],translation=[f(0x574),f(0x578)],layer=layer))
     v=self.rd32(v+0x56c);n+=1
    except Exception:break
  self.rows.append(dict(frame=self.frame,state=state,vms=vms))
r=Probe('games/th13/th13.exe','artifacts/reference/raw',str(out/'draws.jsonl'));r.window='a';r.input_script={};r.run(frames=470)
with (out/'vm-sequence.json').open('w') as f:json.dump({'first':r.first,'rows':r.rows,'crash':r.crash_frame},f,indent=2)
print('first',r.first,'rows',len(r.rows),'crash',r.crash_frame)
