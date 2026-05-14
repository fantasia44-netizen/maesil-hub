import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox
from supabase import create_client, Client

# [보안] Supabase 설정 (이미 확인하신 URL과 Key를 사용하세요)
SUPABASE_URL = "https://pbocckpuiyzijspqpvqz.supabase.co" # 스크린샷 확인 완료
SUPABASE_KEY = "sb_publishable_5TAy2FEAWeRmRCbOz6S14g_x4a8aOYI"

class InitialStockUploader:
    def __init__(self, root):
        self.root = root
        self.root.title("Haeser Initial Stock Uploader v16.50")
        self.root.geometry("450x300")
        
        try:
            self.db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
            status = "✅ DB 연결 성공"
        except:
            status = "❌ DB 연결 실패"
            
        tk.Label(root, text="📦 초기 실재고 일괄 등록", font=("Arial", 14, "bold")).pack(pady=20)
        tk.Label(root, text=status).pack()
        
        # 업로드 가이드
        tk.Label(root, text="엑셀 필수 항목: 품목명, 현재재고, 안전재고", fg="gray").pack(pady=5)
        
        tk.Button(root, text="📂 재고 엑셀파일 선택", command=self.process_upload, 
                  bg="#2E7D32", fg="white", font=("bold", 11), height=2, width=25).pack(pady=20)

    def process_upload(self):
        file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx")])
        if not file_path: return
        
        try:
            df = pd.read_excel(file_path)
            # 필수 컬럼 체크 (Strict Audit) [cite: 2026-01-26]
            required = ['품목명', '현재재고', '안전재고']
            if not all(col in df.columns for col in required):
                messagebox.showerror("오류", f"엑셀 컬럼명이 정확하지 않습니다.\n필요: {required}")
                return

            count = 0
            for _, row in df.iterrows():
                # DB에 있으면 업데이트(Upsert), 없으면 삽입 [cite: 2026-02-10]
                data = {
                    "product_name": str(row['품목명']).strip(),
                    "current_stock": int(row['현재재고']),
                    "safety_stock": int(row['안전재고'])
                }
                # supabase의 upsert 기능을 사용해 중복 등록 방지 [cite: 2026-02-10]
                self.db.table("inventory").upsert(data, on_conflict="product_name").execute()
                count += 1
            
            messagebox.showinfo("완료", f"총 {count}개의 품목이 DB에 등록/갱신되었습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"업로드 실패: {e}")

if __name__ == "__main__":
    root = tk.Tk(); app = InitialStockUploader(root); root.mainloop()