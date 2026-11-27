import time
import random
import sys

# 解決 Windows 終端機 CP950 編碼問題
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from monitor import run_monitor

# ==========================================
# 定時輪詢設定
# ==========================================
CHECK_INTERVAL_MINUTES = 10  # 基礎間隔時間：每 10 分鐘檢查一次

def main():
    print("=" * 65)
    print("🚀 【FB 二手社團自動輪詢監控系統已啟動】")
    print(f"⏰ 檢查頻率：約每 {CHECK_INTERVAL_MINUTES} 分鐘自動巡邏一次")
    print("🛡️ 防封策略：每輪加入隨機微幅延遲，模擬真人不定時查看行為")
    print("💡 隨時按下鍵盤 [Ctrl + C] 即可安全停止監控")
    print("=" * 65)

    round_count = 1
    while True:
        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        print(f"\n▶ 第 {round_count} 輪巡邏開始 ── [{current_time_str}]")

        try:
            run_monitor()
        except KeyboardInterrupt:
            print("\n\n🛑 已收到中斷指令，監控程式已安全停止。")
            break
        except Exception as e:
            print(f"\n⚠️ 本輪執行時發生未預期異常：{e}")
            print("程式將於下個週期自動重試...")

        round_count += 1

        # 隨機延遲（例如 10 分鐘 ± 45 秒），避免死板固定間隔被偵測
        jitter = random.randint(-45, 60)
        sleep_sec = max(180, CHECK_INTERVAL_MINUTES * 60 + jitter)
        next_run = time.strftime('%H:%M:%S', time.localtime(time.time() + sleep_sec))

        print(f"\n💤 正在休息等待中... 下一次巡邏時間預計為：【{next_run}】")
        print("（你可以讓這個視窗縮小放在工作列，它會默默在背景替你守候！）")

        try:
            time.sleep(sleep_sec)
        except KeyboardInterrupt:
            print("\n\n🛑 已收到中斷指令，監控程式已安全停止。")
            break

if __name__ == "__main__":
    main()
