# Antigravity + UnityMCP 連携セットアップガイド (Windows対応版)

AntigravityとUnityMCPを連携させるための、動作確認済みの設定手順です。
Windows環境における特殊な仕様（改行コード問題など）に対応するため、**専用の中継スクリプト（Bridge Script）** を使用する方法が最も確実です。

## 1. Unity側の準備

### パッケージのインストール

1. Unityのメニューから `Window` > `Package Manager` を開く。
2. 左上の `+` > `Add package from git URL...` を選択。
3. 以下のURLを入力してAdd:

   ```
   https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity
   ```

### サーバーの起動

1. メニューの `Window` > `MCP for Unity` (または `MCP`) を開く。
2. **Port** を設定する（例: **8081**）。
3. **Transport** が `HTTP (SSE)` になっていることを確認。
4. **"Start Server"** をクリック。
5. **重要:** 表示されるURLを確認する（例: `http://127.0.0.1:8081`）。

---

## 2. ブリッジスクリプトの作成

Windows環境での通信エラー（Invalid trailing dataなど）やセッション維持の問題を解決するため、以下のPythonスクリプトを作成します。

**ファイル名:** `f:\Antigravity_workspace\UnityMCP_Connection\unity_bridge.py` (このディレクトリ内)

**設定ファイル (新規):** 同ディレクトリに `bridge_config.json` を作成するとURLを変更可能です。

```json
{
  "unity_mcp_url": "http://127.0.0.1:8081/mcp"
}
```

**スクリプト本体:**

```python
import sys
import json
import threading
import requests
import time
import os

# CONFIG: Force Stdout to UTF-8 and LF only (Fixes "invalid trailing data" on Windows)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    except Exception as e:
        pass

# Load Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "bridge_config.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "bridge_debug.log")

try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
        BASE_URL = config.get("unity_mcp_url", "http://127.0.0.1:8081/mcp")
except Exception:
    BASE_URL = "http://127.0.0.1:8081/mcp"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream"
}

ACTIVE_SESSION_ID = None
PRINT_LOCK = threading.Lock()

def log(msg):
    try:
        with open(LOG_FILE, "a", encoding='utf-8') as f:
            f.write(f"[Bridge] {msg}\n")
    except: pass

def safe_print(content):
    with PRINT_LOCK:
        # Write content + newline. 
        # Since we reconfigured stdout, this should be a single LF.
        sys.stdout.write(content + "\n")
        sys.stdout.flush()

def process_sse_line(line):
    """Parse a single SSE line and write JSON payload to stdout if found."""
    if not line: return
    decoded = line.decode('utf-8') if isinstance(line, bytes) else line
    
    if decoded.startswith("data: "):
        content = decoded[6:].strip() # Strip "data: " and whitespace
        if content == "[DONE]": return
        try:
            # Verify if it is valid JSON (sanity check)
            json.loads(content)
            safe_print(content)
            log(f"Forwarded JSON (SSE): {content[:100]}...")
        except:
            log(f"Skipping invalid JSON in SSE data: {content[:100]}")

def poll_sse(session_url):
    log(f"Starting SSE listener on {session_url}")
    try:
        sse_headers = HEADERS.copy()
        if ACTIVE_SESSION_ID:
            sse_headers["Mcp-Session-Id"] = ACTIVE_SESSION_ID
            
        with requests.get(session_url, headers=sse_headers, stream=True, timeout=None) as r:
            if r.status_code != 200:
                log(f"SSE Connection failed: {r.status_code} {r.text}")
                return

            log("SSE Connected and streaming...")
            for line in r.iter_lines():
                if line:
                    process_sse_line(line)
    except Exception as e:
        log(f"SSE Error: {e}")

def main():
    global ACTIVE_SESSION_ID
    # clear log for fresh start
    try:
        with open(LOG_FILE, "w") as f: f.write("Bridge Started (V4 - Binary Safe)\n")
    except: pass
    
    log("Starting Auto-Negotiating UnityMCP Bridge (V4 - LF Newlines)...")

    while True:
        try:
            line = sys.stdin.readline()
            if not line: break
            
            try:
                msg = json.loads(line)
            except: continue

            current_url = BASE_URL
            current_headers = HEADERS.copy()

            if ACTIVE_SESSION_ID:
                current_url = f"{BASE_URL}?sessionId={ACTIVE_SESSION_ID}"
                current_headers["Mcp-Session-Id"] = ACTIVE_SESSION_ID

            is_initialize = msg.get("method") == "initialize"

            try:
                r = requests.post(current_url, json=msg, headers=current_headers, timeout=30)
                
                # Auto-Negotiation
                if is_initialize and not ACTIVE_SESSION_ID:
                    extracted_id = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
                    if extracted_id:
                        ACTIVE_SESSION_ID = extracted_id
                        log(f"Negotiated Session ID: {ACTIVE_SESSION_ID}")
                        sse_url = f"{BASE_URL}?sessionId={ACTIVE_SESSION_ID}"
                        threading.Thread(target=poll_sse, args=(sse_url,), daemon=True).start()
                    else:
                        log("WARNING: No Session ID found in initialize response headers!")

                if r.status_code == 200:
                    content_type = r.headers.get('Content-Type', '')
                    
                    if 'application/json' in content_type:
                         # Pure JSON -> Forward directly
                         safe_print(r.text)
                         log("Responded (JSON)")
                         
                    elif 'text/event-stream' in content_type:
                         # SSE Framed Body -> Must parse!
                         log("Parsing SSE-framed POST response...")
                         for line in r.iter_lines():
                             if line:
                                 process_sse_line(line)
                    else:
                        log(f"Unknown Content-Type: {content_type}")
                        # Fallback try parsing anyway
                        safe_print(r.text)
                        
                else:
                    log(f"POST Failed: {r.status_code} {r.text}")

            except Exception as e:
                log(f"POST Error: {e}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"Loop Error: {e}")
            break

if __name__ == "__main__":
    main()
```

---

## 3. Antigravity側の設定

Antigravityの設定ファイル `mcp_config.json` を編集し、上記のスクリプト経由で接続するようにします。
**注意:** Pythonのパスは環境に合わせて変更してください。

**ファイルパス:** `c:\Users\[ユーザー名]\.gemini\antigravity\mcp_config.json`

```json
{
  "mcpServers": {
    "unityMCP": {
      "type": "stdio",
      "command": "C:\\Users\\simil\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": [
        "f:\\Antigravity_workspace\\UnityMCP_Connection\\unity_bridge.py"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      },
      "disabled": false
    }
  }
}
```

### ✅ 解決される問題

1. **Invalid trailing data error:** スクリプト内で `newline='\n'` を強制することで解決。
2. **Missing Session ID:** スクリプトが自動でIDを取得・保持するため、Unity再起動後も自動復帰。
3. **Encoding Error:** `PYTHONIOENCODING: utf-8` により日本語環境でのクラッシュを回避。

---

## 4. 設定の反映

1. 上記ファイルを保存。
2. Antigravityを **再起動** する。
3. 接続成功を確認（リソース一覧が表示される）。

---

## 5. 技術詳細と重要な注意点 (Critical Technical Notes)

なぜ標準の接続方法ではなく、この「Bridge Script」が必要なのか、その技術的理由とクリティカルな解決策を以下に記録します。将来的なトラブルシューティングの参考にしてください。

### 1. Windows環境における改行コード問題 (CRLF vs LF)

- **問題:** WindowsのPython標準出力 (`sys.stdout`) は、デフォルトで `\n` を `\r\n` (CRLF) に変換して出力します。
- **影響:** MCPプロトコル（JSON-RPC）は厳密なフォーマットを要求するため、余分な `\r` が混入すると、受信側（Antigravity）で "Invalid trailing data" や JSONパースエラーが発生し、通信が切断されます。
- **解決策:** スクリプト冒頭の `sys.stdout.reconfigure(encoding='utf-8', newline='\n')` が最もクリティカルな修正です。これによりWindows上でも強制的にLF改行のみを出力させ、データ破損を防いでいます。

### 2. セッションIDの自動ネゴシエーション

- **問題:** UnityMCPサーバーは、接続ごとにユニークな `sessionId` を発行し、以降の通信でそれを必須とします。Antigravityの標準クライアントは、この「初回レスポンスヘッダからIDを取得して使い回す」という特殊なフローに対応していません。
- **解決策:** Bridge Scriptがプロキシとして振る舞い、サーバーからの初回応答ヘッダ (`Mcp-Session-Id`) をキャプチャし、以降の全リクエストに自動付与しています。これにより、ユーザーが手動でIDを設定する必要がなくなりました。

### 3. SSEとPOSTの並行処理 (Thread Safety)

- **問題:** UnityMCPは、リクエストのレスポンス（POST）と、イベント通知（SSE）の2つの非同期ストリームを使用します。これらが同時に標準出力へ書き込まれると、JSONデータが混ざり合い（インターリーブ）、壊れたJSONとなって送信されます。
- **解決策:** `threading.Lock()` を導入し、標準出力への書き込みを排他制御することで、完全なJSONメッセージのみが送信されるように保証しています。

### 4. 文字コード (Encoding)

- **問題:** 日本語Windows環境のコマンドプロンプトでは、デフォルトエンコーディング（CP932など）により、Unicode文字を含むJSONの送受信でエンコードエラーが発生し、プロセスがクラッシュすることがあります。
- **解決策:** `mcp_config.json` の環境変数設定で `PYTHONIOENCODING: utf-8` を指定し、かつスクリプト内でもUTF-8を明示しています。
