from dataclasses import dataclass
from pathlib import Path
import os
@dataclass(frozen=True)
class Settings:
 token:str|None; owner_id:str|None; db_path:Path
 @classmethod
 def load(cls):
  p=Path('/Users/jerson/AI/runtime/secrets/telegram-bot.env');d={}
  if p.exists():
   for line in p.read_text().splitlines():
    if '=' in line: k,v=line.split('=',1);d[k]=v
  return cls(d.get('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN'),d.get('TELEGRAM_OWNER_ID') or os.getenv('TELEGRAM_OWNER_ID'),Path('/Users/jerson/AI/runtime/control-plane/control-plane.db'))
