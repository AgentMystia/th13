#!/usr/bin/env python3
"""Read-only original title VM snapshots at the same interaction milestones as web_feedback_capture.mjs."""
import json, struct, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oracle.title_oracle import TitleOracle, TITLE_OBJ, VK_Z, VK_RIGHT
OUT=Path('artifacts/th13-longrun-20261029/title-feedback')
OUT.mkdir(parents=True,exist_ok=True)
class Probe(TitleOracle):
 def title_frame(self,out):
  super().title_frame(out)
  t=self.rd32(TITLE_OBJ)
  if not t:return
  state=(self.rd32(t+0x1c),self.rd32(t+0x24),self.rd32(t+0x28))
  if not hasattr(self,'marks'):self.marks={}
  marks=self.marks
  def save(label):
   vms=[]
   for layer in range(33):
    v=self.rd32(self.anm_mgr+0xf48784+layer*0x5a4);n=0
    while v and n<200:
     try:
      script=self._vm_script(v)
      if 3<=script<=22 or 113<=script<=138:
       u16=lambda a:struct.unpack('<H',self.uc.mem_read(a,2))[0]
       f=lambda off:struct.unpack('<f',self.uc.mem_read(v+off,4))[0]
       vms.append(dict(script=script,sprite=u16(v+0x34),color=self.rd32(v+0x528),flags=self.rd32(v+0x18),render_flags=self.rd32(v+0x1c),position=[f(0x3c),f(0x40)],translation=[f(0x574),f(0x578)],scale=[f(0x60),f(0x64)],layer=layer))
      v=self.rd32(v+0x56c);n+=1
     except Exception:break
   marks[label]={'frame':self.frame,'state':state,'vms':vms}
  if state[:2]==(1,2) and 'first_interactive' not in marks:
   save('first_interactive');self.input_script[self.frame+2]=frozenset({VK_Z})
  if state[:2]==(5,2) and 'difficulty_first' not in marks:
   save('difficulty_first');self.input_script[self.frame+1]=frozenset({VK_Z})
  if state[:2]==(6,2) and 'character_first' not in marks:
   save('character_first');self.right_at=self.frame+61;self.input_script[self.right_at]=frozenset({VK_RIGHT})
  if hasattr(self,'right_at') and self.frame==self.right_at-1:save('character_settled')
  if hasattr(self,'right_at') and self.frame==self.right_at+1:save('character_right_first')
  if hasattr(self,'right_at') and self.frame==self.right_at+61:save('character_right_settled')
r=Probe('games/th13/th13.exe','artifacts/reference/raw',str(OUT/'draws.jsonl'));r.window='a';r.input_script={};r.run(frames=600)
with (OUT/'vm-snapshots.json').open('w') as f:json.dump({'marks':getattr(r,'marks',{}),'crash':r.crash_frame},f,indent=2)
print('crash',r.crash_frame,'marks',list(getattr(r,'marks',{})))
