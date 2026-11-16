#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORMGPT X ULTIMATE v2.0 — single-file advanced terminal AI assistant
Platforms: Android Termux + Pydroid 3 (pure Python, only 'requests' required)
Engine: Pollinations Classic API (keyless public inference, GPT-OSS 20B)
        + automatic fallback + retry/backoff + SSE streaming
Toolkit: real TCP scanner, banner grab, TLS cert fingerprint, DNS/crt.sh
         OSINT recon, live NVD CVE lookup, hash identification, net forensics
Scope: educational + defensive security on AUTHORIZED targets only.
       No malware, no credential theft, no destructive tooling — by design.
"""

import os, re, sys, json, time, ssl, socket, random, shutil, base64, binascii
import hashlib, subprocess, threading, urllib.parse, traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:
    import requests
except ImportError:
    print("Missing dependency 'requests'. Install with:  pip install requests")
    sys.exit(1)

# ============================ CONFIG ============================
APP_NAME     = "WORMGPT X ULTIMATE"
VERSION      = "2.0.0"
HISTORY_DIR  = os.path.join(os.path.expanduser("~"), ".wormgptx")
HISTORY_FILE = os.path.join(HISTORY_DIR, "history.json")
CONN_TIMEOUT = 25
MAX_RETRIES  = 3
STREAM_ENABLED = True

# Keyless public endpoints (no user-supplied key). Anonymous tier of the
# Pollinations Classic API is rate-limited per IP (~1 text req / 30s):
# the engine retries with backoff and falls back between providers.
PROVIDERS = [
    {"name": "Pollinations OpenAI-compat", "url": "https://text.pollinations.ai/openai",
     "model": "openai-fast", "type": "openai", "stream": True},
    {"name": "Pollinations Plain",         "url": "https://text.pollinations.ai/",
     "model": "openai-fast", "type": "plain",  "stream": False},
]
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "WormGPTX-Ultimate/2.0 (educational terminal assistant)",
}

# ============================ COLORS / UI ============================
class C:
    RESET="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
    RED="\033[91m"; DRED="\033[31m"; GREEN="\033[92m"; DGREEN="\033[32m"
    YELLOW="\033[93m"; CYAN="\033[96m"; WHITE="\033[97m"

if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    for a in list(vars(C)):
        if not a.startswith("_"): setattr(C, a, "")

BANNER = r"""
 █████╗ ███████╗ █████╗ ██████╗ ██╗  ██╗ ████████╗██╗  ██╗
██╔══██╗██╔════╝██╔══██╗██╔══██╗██║ ██╔╝ ╚══██╔══╝╚██╗██╔╝
███████║███████╗███████║██████╔╝█████╔╝     ██║    ╚███╔╝
██╔══██║╚════██║██╔══██║██╔══██╗██╔═██╗     ██║    ██╔██╗
██║  ██║███████║██║  ██║██║  ██║██║  ██╗██╗██║██╗██╔╝ ██╗
╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝╚═╝╚═╝  ╚═╝
                 X   U L T I M A T E   v2.0
"""

def bell(): sys.stdout.write("\a"); sys.stdout.flush()
def clear(): os.system("cls" if os.name == "nt" else "clear")

def type_out(text, cps=1200):
    delay = 1.0 / cps
    for ch in text:
        sys.stdout.write(ch); sys.stdout.flush()
        if delay: time.sleep(delay)
    sys.stdout.write("\n")

def spinner(stop_flag, label="working"):
    frames = "|/-\\"; i = 0
    while stop_flag[0]:
        sys.stdout.write(f"\r{C.DRED}  [{frames[i % 4]}] {label}...{C.RESET}   ")
        sys.stdout.flush(); time.sleep(0.12); i += 1
    sys.stdout.write("\r" + " " * 70 + "\r"); sys.stdout.flush()

def loading_screen():
    for label, dur in [("initializing core", 0.3), ("loading mode matrix", 0.25),
                       ("probing inference endpoints", 0.6),
                       ("loading live toolkit", 0.3),
                       ("arming terminal interface", 0.25)]:
        stop = [True]
        t = threading.Thread(target=spinner, args=(stop, label), daemon=True)
        t.start(); time.sleep(dur); stop[0] = False; t.join(timeout=1)

def print_banner():
    print(C.RED + C.BOLD + BANNER + C.RESET)
    print(C.DRED + f"  v{VERSION} | Termux + Pydroid 3 | keyless online AI + live toolkit" + C.RESET)
    print(C.DIM + "  Educational & defensive security on authorized targets only." + C.RESET)
    print()

HELP_TEXT = f"""{C.YELLOW}{C.BOLD}COMMANDS{C.RESET}
  /help /chat /code /debug /cyber /osint /linux /network /system
  /model /status /history /save [name] /clear /exit
  --- LIVE TOOLKIT (authorized targets only) ---
  /scan <host> [ports]   threaded TCP scan + banner grab + TLS cert
  /recon <domain>        DNS enum + crt.sh cert-transparency subdomains
  /dns <domain>          A / PTR / common-sub resolution
  /cve <product>         live NVD CVE + CVSS lookup (e.g. /cve nginx 1.20)
  /hash <hash>           hash format identification (offline)
  /geo <ip|domain>       network ownership / geolocation intel
  /netinfo               this device: local IP, iface, gateway, public IP
  /nmap [args]           real nmap -sV top-100 if installed (Termux)
Any other input goes to the AI. Multi-line: end line with \\ and continue."""

# ============================ AI MODES ============================
MODES = {
    "general": {"label": "GENERAL AI", "system":
        "You are WormGPT X, a helpful technical AI assistant running in an Android "
        "terminal. Answer clearly and concisely: research, explanations, translation, "
        "problem solving. Terminal-friendly plain-text formatting."},
    "code": {"label": "AI CODER", "system":
        "You are WormGPT X CODER, an expert programming assistant for Python, HTML, "
        "CSS, JavaScript, Bash and Termux shell scripting. Always output complete "
        "runnable code in fenced blocks, then a brief explanation. Diagnose root "
        "cause of errors first, then give fixed code."},
    "debug": {"label": "DEBUG AI", "system":
        "You are WormGPT X DEBUG. Users paste errors, tracebacks and broken code. "
        "Identify root cause, explain why, then provide the corrected version. Be "
        "precise and terse."},
    "cyber": {"label": "CYBER AI", "system":
        "You are WormGPT X CYBER, a cybersecurity EDUCATION assistant: ethical hacking "
        "concepts, Linux/Termux security, network security, defensive auditing, OSINT "
        "methodology, Android security, CTF walkthroughs, vulnerability analysis for "
        "AUTHORIZED labs. Refuse malware creation, credential theft, unauthorized "
        "access and destructive techniques; teach the defensive concept instead. "
        "Cite OWASP / MITRE ATT&CK where relevant."},
    "osint": {"label": "OSINT MODE", "system":
        "You are WormGPT X OSINT, an open-source intelligence EDUCATION assistant. "
        "Teach OSINT methodology, tooling concepts and legal/ethical intelligence "
        "gathering. Never assist stalking, doxxing or targeting private individuals."},
    "system": {"label": "SYSTEM AI", "system":
        "You are WormGPT X SYSTEM, a Linux/Termux/Android troubleshooting assistant. "
        "Explain commands step by step, diagnose Termux and Python packaging problems, "
        "guide environment diagnostics. Give exact commands to paste."},
}
CMD_MODE = {"/chat": "general", "/code": "code", "/debug": "debug",
            "/cyber": "cyber", "/osint": "osint", "/linux": "cyber",
            "/network": "cyber", "/system": "system"}

# ============================ AI ENGINE ============================
class Engine:
    def __init__(self):
        self.provider_idx = 0
        self.active_provider = None
        self.active_model = "unknown"
        self.last_ok = None

    def _post(self, url, body):
        return requests.post(url, json=body, headers=HEADERS, timeout=CONN_TIMEOUT)

    def probe(self):
        for i, p in enumerate(PROVIDERS):
            try:
                r = self._post(p["url"], {"model": p["model"], "stream": False,
                                          "messages": [{"role": "user", "content": "ping"}]})
                if r.status_code == 200:
                    self.provider_idx, self.active_provider = i, p["name"]
                    self.active_model = p["model"]
                    self.last_ok = datetime.now().strftime("%H:%M:%S")
                    return True, p["name"]
                if r.status_code == 429:
                    self.provider_idx, self.active_provider = i, p["name"] + " (rate-limited)"
                    self.active_model = p["model"]
                    return True, f"{p['name']} (reachable, rate-limited)"
            except requests.RequestException:
                continue
        return False, None

    def _extract_plain(self, resp):
        txt = resp.text.strip()
        try:
            d = resp.json()
            if isinstance(d, dict):
                txt = (d.get("choices", [{}])[0].get("message", {}).get("content")
                       or d.get("content") or txt)
        except (ValueError, IndexError, KeyError):
            pass
        return txt

    def _stream_openai(self, messages):
        p = PROVIDERS[self.provider_idx]
        r = self._post(p["url"], {"model": p["model"], "stream": True, "messages": messages})
        r.raise_for_status()
        collected = []
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"): continue
            chunk = raw[5:].strip()
            if chunk == "[DONE]": break
            try:
                piece = json.loads(chunk)["choices"][0].get("delta", {}).get("content", "")
            except (ValueError, KeyError, IndexError):
                continue
            if piece:
                collected.append(piece)
                sys.stdout.write(C.GREEN + piece + C.RESET); sys.stdout.flush()
        print()
        return "".join(collected)

    def ask(self, messages):
        last_err = None
        for attempt in range(MAX_RETRIES):
            p = PROVIDERS[self.provider_idx]
            try:
                if p["type"] == "openai":
                    if p.get("stream") and STREAM_ENABLED:
                        text = self._stream_openai(messages)
                        self.last_ok = datetime.now().strftime("%H:%M:%S")
                        return text, p["name"], None
                    r = self._post(p["url"], {"model": p["model"], "stream": False,
                                              "messages": messages})
                    if r.status_code == 429:
                        raise requests.HTTPError("429 rate-limited")
                    r.raise_for_status()
                    return self._extract_plain(r), p["name"], None
                else:
                    r = self._post(p["url"], {"messages": messages, "model": p["model"]})
                    r.raise_for_status()
                    return self._extract_plain(r), p["name"], None
            except requests.RequestException as e:
                last_err = f"{p['name']}: {e}"
                self.provider_idx = (self.provider_idx + 1) % len(PROVIDERS)
                time.sleep(2 * (attempt + 1))  # backoff also clears rate limits
        return None, None, last_err

# ============================ MEMORY ============================
class Memory:
    def __init__(self): self.history = []

    def add(self, role, content):
        self.history.append({"role": role, "content": content,
                             "ts": datetime.now().isoformat(timespec="seconds")})

    def messages(self, system_prompt, limit=12):
        return ([{"role": "system", "content": system_prompt}] +
                [{"role": h["role"], "content": h["content"]}
                 for h in self.history[-limit:]])

    def clear(self): self.history = []

    def save(self, name=None):
        os.makedirs(HISTORY_DIR, exist_ok=True)
        path = HISTORY_FILE
        if name:
            path = os.path.join(HISTORY_DIR, f"chat_{re.sub(r'[^\w-]', '_', name)}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
            return path
        except OSError as e:
            return f"save failed: {e}"

    def load_persisted(self):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                self.history = json.load(f)[-60:]
        except (OSError, ValueError):
            pass

    def last_n(self, n=10): return self.history[-n:]

# ============================ LIVE TOOLKIT ============================
SERVICE_PROBES = {21:"ftp",22:"ssh",23:"telnet",25:"smtp",53:"dns",80:"http",
    110:"pop3",111:"rpc",135:"msrpc",139:"netbios",143:"imap",443:"https",
    445:"smb",993:"imaps",995:"pop3s",1433:"mssql",1723:"pptp",3306:"mysql",
    3389:"rdp",5432:"postgres",5900:"vnc",6379:"redis",8080:"http-alt",
    8443:"https-alt",9200:"elasticsearch",27017:"mongodb"}
TOOL_TIMEOUT = 8

def _sock_scan(host, ports, timeout=1.5):
    def probe(port):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return (port, True)
        except (socket.timeout, ConnectionRefusedError, OSError):
            return (port, False)
    with ThreadPoolExecutor(max_workers=100) as ex:
        results = list(ex.map(probe, ports))
    return [p for p, ok in sorted(results) if ok]

def grab_banner(host, port, timeout=3):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        if port in (443, 8443):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(s, server_hostname=host)
        if port in (80, 8080, 8000, 443, 8443):
            s.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        s.settimeout(timeout)
        data = s.recv(1024); s.close()
        return data.decode(errors="replace").strip()[:400]
    except Exception as e:
        return f"(no banner: {e.__class__.__name__})"

def tls_cert(host, port=443):
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=TOOL_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                fp = hashlib.sha256(der).hexdigest()
                cert = tls.getpeercert()
                return {"issuer": dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "?"),
                        "subject_cn": dict(x[0] for x in cert.get("subject", ())).get("commonName", "?"),
                        "sha256_fp": ":".join(fp[i:i+2] for i in range(0, 16, 2)) + "..."}
    except Exception as e:
        return {"error": str(e)}

def dns_records(domain):
    recs = {}
    try:
        recs["A"] = sorted({ai[4][0] for ai in
                            socket.getaddrinfo(domain, None, socket.AF_INET)})
    except socket.gaierror:
        recs["A"] = None
    for ip in recs.get("A") or []:
        try: recs.setdefault("PTR", []).append(socket.gethostbyaddr(ip)[0])
        except (socket.herror, OSError): pass
    found = []
    for s in ("www","mail","vpn","dev","staging","api","admin","ns1","ns2","git",
              "cpanel","remote"):
        fq = f"{s}.{domain}"
        try: found.append(f"{fq} -> {socket.gethostbyname(fq)}")
        except socket.gaierror: pass
    recs["subdomains"] = found
    return recs

def crtsh_subdomains(domain):
    try:
        r = requests.get(f"https://crt.sh/?q=%25.{domain}&output=json",
                         timeout=20, headers=HEADERS)
        if r.status_code != 200:
            return [f"crt.sh HTTP {r.status_code}"]
        return sorted({n for row in r.json()
                       for n in row.get("name_value", "").split("\n")
                       if n and "*" not in n})[:80]
    except (requests.RequestException, ValueError):
        return ["crt.sh unreachable"]

def nvd_cve(query, max_results=8):
    try:
        r = requests.get("https://services.nvd.nist.gov/rest/json/cves/2.0",
                         params={"keywordSearch": query, "resultsPerPage": max_results},
                         headers=HEADERS, timeout=20)
        r.raise_for_status()
        out = []
        for item in r.json().get("vulnerabilities", []):
            c = item["cve"]; metrics = c.get("metrics", {}); cvss = None
            for k in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                if k in metrics:
                    cvss = metrics[k][0]["cvssData"].get("baseScore"); break
            desc = next((d["value"] for d in c["descriptions"] if d["lang"] == "en"), "")
            out.append(f"{c['id']}  CVSS:{cvss or '?'}\n  {desc[:220]}")
        return out or ["no CVEs matched"]
    except requests.RequestException as e:
        return [f"NVD error: {e}"]

def identify_hash(h):
    h = h.strip().lower()
    if re.fullmatch(r"\$2[aby]\$\d{2}\$[\w./]+", h): return ["bcrypt"]
    for pre, name in (("$6$", "SHA-512crypt"), ("$5$", "SHA-256crypt"),
                      ("$1$", "MD5crypt")):
        if h.startswith(pre): return [name]
    if h.startswith("$argon2"): return ["Argon2"]
    for ln, name in ((32,"MD5"),(40,"SHA-1"),(56,"SHA-224"),(64,"SHA-256"),
                     (96,"SHA-384"),(128,"SHA-512")):
        if re.fullmatch(r"[0-9a-f]{%d}" % ln, h): return [name]
    return ["unknown format"]

def netinfo():
    info = {"hostname": socket.gethostname()}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); info["local_ip"] = s.getsockname()[0]; s.close()
    except OSError:
        info["local_ip"] = "unavailable"
    try:
        info["public_ip"] = requests.get("https://api.ipify.org", timeout=10).text
    except requests.RequestException:
        info["public_ip"] = "unreachable"
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[1] == "00000000":
                    info["default_gateway"] = socket.inet_ntoa(
                        bytes.fromhex(parts[2])[::-1])
                    info["iface"] = parts[0]; break
    except OSError:
        pass
    return info

def expand_ports(spec):
    ports = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-"); ports.update(range(int(a), int(b) + 1))
        elif part:
            ports.add(int(part))
    return sorted(p for p in ports if 0 < p < 65536)[:4096]

def run_tool(name, args):
    L = lambda s: print(s)
    if name == "/scan":
        if not args: return "usage: /scan <host> [port-range e.g. 1-1024]"
        parts = args.split(); host = parts[0]
        try: ip = socket.gethostbyname(host)
        except socket.gaierror: return f"cannot resolve {host}"
        ports = expand_ports(parts[1]) if len(parts) > 1 else list(SERVICE_PROBES)
        L(f"  {C.RED}target{C.RESET} {host} -> {ip}   {C.DIM}{len(ports)} ports{C.RESET}")
        open_ports = _sock_scan(ip, ports)
        if not open_ports:
            L(f"  {C.YELLOW}no open TCP ports in range{C.RESET}"); return None
        for p in open_ports:
            L(f"  {C.GREEN}{p}/tcp{C.RESET} open  {C.CYAN}{SERVICE_PROBES.get(p,'?')}{C.RESET}")
        for p in open_ports[:5]:
            L(f"  {C.DIM}banner :{p}{C.RESET} {grab_banner(ip, p)[:200]}")
        if 443 in open_ports:
            L(f"  {C.DIM}tls{C.RESET} {tls_cert(ip)}")
        return None
    elif name == "/recon":
        if not args: return "usage: /recon <domain>"
        L(f"  {C.RED}OSINT recon:{C.RESET} {args}")
        for k, v in dns_records(args).items():
            L(f"  {C.CYAN}{k}{C.RESET}: {v}")
        subs = crtsh_subdomains(args)
        L(f"  {C.CYAN}crt.sh subdomains ({len(subs)}){C.RESET}:")
        for s in subs[:30]: L(f"    {s}")
        return None
    elif name == "/cve":
        if not args: return "usage: /cve <product>"
        for r in nvd_cve(args): L(f"  {C.YELLOW}{r}{C.RESET}")
        return None
    elif name == "/hash":
        if not args: return "usage: /hash <hashstring>"
        return f"  identified: {identify_hash(args)}"
    elif name == "/dns":
        if not args: return "usage: /dns <domain>"
        for k, v in dns_records(args).items(): L(f"  {C.CYAN}{k}{C.RESET}: {v}")
        return None
    elif name == "/netinfo":
        for k, v in netinfo().items(): L(f"  {C.CYAN}{k}{C.RESET}: {v}")
        return None
    elif name == "/nmap":
        if shutil.which("nmap"):
            out = subprocess.run(["nmap", "-sV", "--top-ports", "100", *args.split()],
                                 capture_output=True, text=True, timeout=300)
            L((out.stdout or out.stderr)[-3000:]); return None
        return "nmap not installed. Termux: pkg install nmap -y (or use /scan)"
    elif name == "/geo":
        if not args: return "usage: /geo <ip-or-domain>"
        try:
            ip = socket.gethostbyname(args)
            return f"  {requests.get(f'https://ipinfo.io/{ip}/json', timeout=10).json()}"
        except (socket.gaierror, requests.RequestException, ValueError) as e:
            return f"  lookup failed: {e}"
    return f"unknown tool {name}"

# ============================ MAIN LOOP ============================
def status_line(engine):
    state = (C.GREEN + "ONLINE" + C.RESET if engine.active_provider
             else C.RED + "OFFLINE" + C.RESET)
    print(f"  {C.DIM}connection:{C.RESET} {state}  "
          f"{C.DIM}model:{C.RESET} {C.CYAN}{engine.active_model}{C.RESET}  "
          f"{C.DIM}last ok:{C.RESET} {engine.last_ok or '-'}")

def show_history(mem):
    if not mem.last_n():
        print(C.DIM + "  (empty)" + C.RESET); return
    for h in mem.last_n(10):
        who = C.RED + "YOU" + C.RESET if h["role"] == "user" else C.GREEN + "AI " + C.RESET
        print(f"  {who} {C.DIM}[{h['ts'][11:16]}]{C.RESET} "
              f"{h['content'][:140].replace(chr(10), ' ')}")

def main():
    clear(); print_banner(); bell(); loading_screen()
    engine = Engine(); mem = Memory(); mem.load_persisted(); mode = "general"

    print(C.YELLOW + "  probing inference endpoints..." + C.RESET)
    ok, name = engine.probe()
    print(C.GREEN + f"  [+] connected: {name}" + C.RESET if ok else
          C.RED + "  [!] no endpoint reachable — check internet. Will retry on send." + C.RESET)
    status_line(engine)
    print(C.YELLOW + HELP_TEXT + C.RESET)

    while True:
        try:
            prompt = input(f"\n{C.RED}{C.BOLD}[{MODES[mode]['label']}]{C.RESET}"
                           f"{C.GREEN} you > {C.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + C.RED + "  interrupted. /exit to quit." + C.RESET); continue
        if not prompt: continue

        if prompt.startswith("/"):
            cmd, _, arg = prompt.partition(" "); cmd = cmd.lower()
            if cmd == "/exit":
                mem.save(); print(C.RED + "  session saved. shutting down." + C.RESET); break
            elif cmd == "/help": print(C.YELLOW + HELP_TEXT + C.RESET)
            elif cmd in CMD_MODE: mode = CMD_MODE[cmd]
            elif cmd == "/model":
                for i, p in enumerate(PROVIDERS):
                    mark = (C.GREEN + " <- active" + C.RESET
                            if i == engine.provider_idx else C.DIM)
                    print(f"  [{i}] {p['name']}  model={p['model']}{mark}")
            elif cmd == "/status":
                ok, name = engine.probe()
                print((C.GREEN + f"  [+] reachable: {name}" + C.RESET) if ok else
                      (C.RED + "  [!] unreachable" + C.RESET))
                status_line(engine)
            elif cmd == "/history": show_history(mem)
            elif cmd == "/save":
                print(C.GREEN + f"  saved -> {mem.save(arg.strip() or None)}" + C.RESET)
            elif cmd == "/clear":
                mem.clear(); mem.save(); print(C.YELLOW + "  memory cleared." + C.RESET)
            elif cmd in ("/scan","/recon","/cve","/hash","/dns","/netinfo","/nmap","/geo"):
                result = run_tool(cmd, arg)
                if result: print(C.GREEN + result + C.RESET)
                try:
                    follow = input(f"{C.DIM}  ask CYBER AI to analyze results? [y/N]{C.RESET} "
                                   ).strip().lower()
                    if follow == "y":
                        mode = "cyber"
                        prompt = (f"Analyze these live tool results from '{cmd} {arg}' and "
                                  f"suggest next authorized assessment steps:\n{result or 'see above'}")
                        mem.add("user", prompt)
                        messages = mem.messages(MODES[mode]["system"])
                        text, _, err = engine.ask(messages)
                        if text: type_out(C.GREEN + text + C.RESET); mem.add("assistant", text)
                        else: print(C.RED + f"  [!] AI failed: {err}" + C.RESET)
                except (EOFError, KeyboardInterrupt):
                    pass
                continue
            else:
                print(C.RED + f"  unknown command: {cmd} (/help)" + C.RESET)
            print(C.DIM + f"  mode: {MODES[mode]['label']}" + C.RESET)
            continue

        if prompt.startswith("\\"):
            extra = input("... > ").strip()
            prompt = prompt[1:] + " " + extra

        mem.add("user", prompt)
        messages = mem.messages(MODES[mode]["system"])
        stop = [True]
        t = threading.Thread(target=spinner,
                             args=(stop, "querying " + engine.active_model), daemon=True)
        t.start()
        text, provider, err = engine.ask(messages)
        stop[0] = False; t.join(timeout=1)

        if text is None:
            print(C.RED + f"  [!] all providers failed: {err}" + C.RESET)
            print(C.DIM + "  anonymous tier is rate-limited (~1 req/30s). "
                  "Wait and retry, or check your connection." + C.RESET)
            mem.history.pop(); continue
        if text:
            type_out(C.GREEN + text + C.RESET)
        else:
            text = "(streamed above)"
        mem.add("assistant", text)
        status_line(engine)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n" + C.RED + "  terminated." + C.RESET)
    except Exception:
        print(C.RED + "  fatal error:" + C.RESET); traceback.print_exc()
