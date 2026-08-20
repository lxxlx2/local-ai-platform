from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

MENU = "🤖 本地 AI 控制中心\n\n请选择一个功能："
BUTTONS = [["✅ 待我审批", "📋 任务中心"], ["📁 项目", "🧠 模型"], ["💻 系统状态", "⚙️ 功能管理"], ["📊 报告", "🔧 设置"]]


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=item) for item in row] for row in BUTTONS], resize_keyboard=True)


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows])


BACK = inline([[("⬅️ 返回首页", "home")]])
