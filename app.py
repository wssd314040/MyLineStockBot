# app.py

import os
import json   # ← 新增這行
import requests
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# ---------------------------------------------------
# 從環境變數讀取 Channel Secret / Channel Access Token
# 你必須事先在系統環境變數裡 export 這兩個值
# ---------------------------------------------------
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_TOKEN  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

if not CHANNEL_SECRET or not CHANNEL_TOKEN:
    raise ValueError("請先設定環境變數：LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(CHANNEL_TOKEN)
handler      = WebhookHandler(CHANNEL_SECRET)

# ---------------------------------------------------
# 輔助函式：呼叫 TWSE 即時股價 API，
# 取得開盤、最高、最低、昨收、最新價，並計算漲跌、漲跌幅
#
# API 範例 URL: https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw
# JSON 回傳結構中：
#   'o' → 開盤價 (Open)
#   'h' → 當天最高價 (High)
#   'l' → 當天最低價 (Low)
#   'y' → 昨收價 (Yesterday's close)
#   'z' → 最新成交價 (Last Price)
# 若欄位為 "-" 或空字串，視為 None
# ---------------------------------------------------
def get_stock_info(stock_code: str) -> dict:
    """
    輸入台股代號 (例如 "2330"), 回傳 dict，包含：
      - open (開盤)
      - high (最高)
      - low  (最低)
      - prev (昨收)
      - last (最新成交)
      - change     (最新 - 昨收)
      - change_pct (漲跌幅 %)
    若該欄位不存在或股未成交，對應值為 None。
    """
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{stock_code}.tw"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        # 1) Debug：印出原始 JSON 內容，方便確認哪些欄位有值
        print(f"【DEBUG】{stock_code} API 原始回傳：")
        print(json.dumps(data, ensure_ascii=False, indent=2))

        if "msgArray" in data and len(data["msgArray"]) > 0:
            info = data["msgArray"][0]

            raw_open  = info.get("o", "")
            raw_high  = info.get("h", "")
            raw_low   = info.get("l", "")
            raw_prev  = info.get("y", "")
            raw_last  = info.get("z", "")

            open_price = raw_open  if raw_open  and raw_open  != "-" else None
            high_price = raw_high  if raw_high  and raw_high  != "-" else None
            low_price  = raw_low   if raw_low   and raw_low   != "-" else None
            prev_close = raw_prev  if raw_prev  and raw_prev  != "-" else None
            last_price = raw_last  if raw_last  and raw_last  != "-" else None

            change      = None
            change_pct  = None
            if prev_close is not None and last_price is not None:
                try:
                    prev_f = float(prev_close.replace(",", ""))
                    last_f = float(last_price.replace(",", ""))
                    change     = round(last_f - prev_f, 2)
                    if prev_f != 0:
                        change_pct = round((last_f - prev_f) / prev_f * 100, 2)
                except ValueError:
                    change = None
                    change_pct = None

            return {
                "open"      : open_price,
                "high"      : high_price,
                "low"       : low_price,
                "prev"      : prev_close,
                "last"      : last_price,
                "change"    : change,
                "change_pct": change_pct
            }

        # 若 msgArray 為空
        return {
            "open"      : None,
            "high"      : None,
            "low"       : None,
            "prev"      : None,
            "last"      : None,
            "change"    : None,
            "change_pct": None
        }

    except Exception as e:
        app.logger.error(f"抓取股價失敗: {e}")
        return {
            "open"      : None,
            "high"      : None,
            "low"       : None,
            "prev"      : None,
            "last"      : None,
            "change"    : None,
            "change_pct": None
        }


# ---------------------------------------------------
# Webhook 主入口：LINE 伺服器會把所有事件 POST 到 /callback
# ---------------------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# ---------------------------------------------------
# 處理使用者傳來的文字訊息 (MessageEvent、TextMessage)
# 回傳內容包含：開盤、最高、最低、昨收、最新、漲跌、漲跌幅
# ---------------------------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.strip()
    codes = [c.strip() for c in text.split(",") if c.strip().isdigit()]

    if len(codes) == 0:
        reply = "請輸入正確的台股代號，例如「2330」或「2330,0050」"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    replies = []
    for code in codes:
        info = get_stock_info(code)
        open_price   = info["open"]
        high_price   = info["high"]
        low_price    = info["low"]
        prev_close   = info["prev"]
        last_price   = info["last"]
        change       = info["change"]
        change_pct   = info["change_pct"]

        if last_price is None:
            replies.append(f"{code} 找不到即時報價或尚未成交。")
        else:
            open_str       = open_price      if open_price   is not None else "—"
            high_str       = high_price      if high_price   is not None else "—"
            low_str        = low_price       if low_price    is not None else "—"
            prev_str       = prev_close      if prev_close   is not None else "—"
            last_str       = last_price
            change_str     = f"{change:+.2f}"       if change       is not None else "—"
            change_pct_str = f"{change_pct:+.2f}%"  if change_pct   is not None else "—"

            replies.append(
                f"{code} 開盤價：{open_str} 元\n"
                f"      當日最高：{high_str} 元\n"
                f"      當日最低：{low_str} 元\n"
                f"      昨收價：{prev_str} 元\n"
                f"      最新成交：{last_str} 元\n"
                f"      漲跌值：{change_str} 元\n"
                f"      漲跌幅：{change_pct_str}"
            )

    full_reply = "\n\n".join(replies)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=full_reply))


if __name__ == "__main__":
    import os
    # 讀取 Render 給的 PORT，如果沒有就用 5001
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)

