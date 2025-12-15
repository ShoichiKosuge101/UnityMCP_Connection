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
