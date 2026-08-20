import asyncio
from aiogram import Bot,Dispatcher,F
from aiogram.types import Message,ReplyKeyboardMarkup,KeyboardButton
from local_ai_control.config.settings import Settings
from local_ai_control.bot.ui import MENU,BUTTONS

def keyboard(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in row] for row in BUTTONS],resize_keyboard=True)
async def run():
 s=Settings.load()
 if not s.token or not s.owner_id: raise RuntimeError('ACTION_REQUIRED: TELEGRAM_BOT_CREDENTIALS')
 bot=Bot(s.token);dp=Dispatcher()
 @dp.message(F.text.in_({'/start','💻 系统状态'}))
 async def home(m:Message):
  if str(m.from_user.id)!=str(s.owner_id): return
  await m.answer(MENU,reply_markup=keyboard())
 await dp.start_polling(bot)
if __name__=='__main__': asyncio.run(run())
