import json
import time
import sys
import os
import re
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print('Missing dependencies: pip install requests beautifulsoup4')
    sys.exit(1)

TARGET_FILE = sys.argv[1] if len(sys.argv) > 1 else r'E:\codes\mini_claude_code\.claude\skills\browser-cdp\data\target_websites_new.json'
OUTPUT_DIR = sys.argv[2] if len(sys.argv) > 2 else r'E:\codes\mini_claude_code\.agent\daemon_run_outputs\goals\goal_64082644\run_0123'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


def detect_cloudflare(resp):
    text = resp.text
    if resp.status_code == 403:
        return True, 'HTTP 403 Forbidden'
    if 'cloudflare' in text.lower() or 'cloudflare' in str(resp.headers).lower():
        return True, 'Cloudflare detected in response'
    if 'cf-ray' in resp.headers or 'cf-cache-status' in resp.headers:
        return True, 'Cloudflare response headers'
    if 'sorry' in text.lower() and 'cloudflare' in text.lower():
        return True, 'Cloudflare sorry page'
    return False, ''


def detect_waf(resp):
    text = resp.text
    headers_str = str(resp.headers)
    signals = []
    waf_keywords = {
        'aws_waf': ['waf', 'blocked_request'],
        'js_challenge': ['__jschal__', '__jsluid', '__jsl'],
        'geetest': ['geetest', 'gt.js'],
        'slide_captcha': ['slide', 'verify', 'captcha', 'ucaptcha'],
        'qianxin': ['qax', 'safe3', '墙盾'],
        'knownsec': ['knownsec', 'ks-waf'],
        'huawei': ['huawei', 'hws-waf'],
        'baidu': ['baidu.com/safe', 'baidusec'],
    }
    for name, kws in waf_keywords.items():
        for kw in kws:
            if kw.lower() in text.lower() or kw.lower() in headers_str.lower():
                signals.append(name)
                break
    return len(signals) > 0, signals


def detect_captcha(resp):
    text = resp.text
    signals = []
    captcha_patterns = [
        (r'geetest', 'Geetest'),
        (r'captcha', 'Captcha'),
        (r'slide.*[vvVv]erif', 'Slide verify'),
        (r'point.*[vvVv]erif', 'Point verify'),
        (r'slidecaptcha', 'SlideCaptcha'),
        (r'__jschal__|__jschl__', 'JS challenge'),
        (r'cf-[cC]hallenge|challenge-phase', 'CF challenge'),
        (r'ua[rt]s', 'Urt'),
        (r'nc_1[st]|nc-code', 'NetEase Captcha'),
    ]
    for pat, name in captcha_patterns:
        if re.search(pat, text, re.I):
            signals.append(name)
    return len(signals) > 0, signals


def detect_signature(resp):
    text = resp.text
    signals = []
    if re.search(r'__signature__|__token__|__curtime__|__sp__', text):
        signals.append('signature/token')
    if re.search(r'encrypt|crypto|aes|rsa', text, re.I):
        signals.append('encryption')
    if re.search(r'window\.[_a-z0-9]+\s*=', text, re.I):
        signals.append('obfuscated_js')
    if re.search(r'eval\(', text, re.I):
        signals.append('eval_usage')
    if resp.url and any(p in resp.url.lower() for p in ['api', 'ajax', 'json']):
        if 'sign' in resp.text.lower() or 'signature' in resp.text.lower():
            signals.append('api_signature')
    return len(signals) > 0, signals


def detect_dom_features(resp):
    text = resp.text
    soup = BeautifulSoup(text, 'html.parser')
    features = {}
    
    # Shadow DOM
    shadow_dom = soup.select('[shadow]')
    features['shadow_dom_elements'] = len(shadow_dom)
    
    # iframe
    iframes = soup.find_all('iframe')
    features['iframe_count'] = len(iframes)
    features['iframe_sources'] = [i.get('src', '')[:80] for i in iframes[:5]]
    
    # Dynamic rendering indicators
    scripts = soup.find_all('script')
    features['script_count'] = len(scripts)
    features['inline_script_count'] = sum(1 for s in scripts if s.string)
    
    # React/Vue/Angular markers
    react_el = soup.select('[data-reactroot], [v-application], [ng-app]')
    features['spa_framework_detected'] = len(react_el) > 0
    
    # SSR vs SPA
    has_data_json = bool(soup.find('script', type='application/ld+json'))
    has_server_rendered = bool(re.search(r'<!--SERVER_RENDERED|--\s*SSR', text))
    features['has_jsonld'] = has_data_json
    features['is_ssr'] = has_server_rendered
    
    # Dynamic content indicators
    features['has_ajax_call'] = bool(re.search(r'fetch\(|XMLHttpRequest|axios', text))
    features['has_vue_instance'] = bool(re.search(r'new\s+Vue|__vue__', text))
    features['has_react_instance'] = bool(re.search(r'reactDOM\.render|__reactInternalInstance', text))
    
    return features


def extract_key_selectors(resp):
    text = resp.text
    soup = BeautifulSoup(text, 'html.parser')
    
    # Extract common stable selectors
    selectors = {
        'logo': None,
        'search_box': None,
        'main_content': None,
        'navigation': None,
    }
    
    # Logo
    logo = soup.find('img', alt=re.compile(r'logo|icon', re.I))
    if logo:
        selectors['logo'] = 'img[alt*=logo]'
    else:
        logo = soup.select_one('.logo, .logo-img, #logo, [class*="logo"]')
        selectors['logo'] = str(logo) if logo else None
    
    # Search box
    search = soup.find('input', {'type': 'text', 'placeholder': re.compile(r'search|搜索|查询', re.I)})
    if search:
        selectors['search_box'] = f"input[placeholder*={search.get('placeholder', '')}]"
    else:
        search = soup.select_one('input[placeholder*="搜索"], input[placeholder*="search"], .search-input, #search')
        selectors['search_box'] = str(search) if search else None
    
    # Main content
    main = soup.select_one('main, #main, .main, .content, article')
    selectors['main_content'] = str(main) if main else None
    
    # Navigation
    nav = soup.select_one('nav, .nav, #nav, header nav')
    selectors['navigation'] = str(nav) if nav else None
    
    return selectors


def probe_site(name, url, priority, category, timeout=15):
    result = {
        'name': name,
        'url': url,
        'priority': priority,
        'category': category,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        result['http_status'] = resp.status_code
        result['response_time_ms'] = int(resp.elapsed.total_seconds() * 1000)
        result['content_length'] = len(resp.text)
        result['encoding'] = resp.encoding
        result['final_url'] = str(resp.url)
        
        # Anti-scraping detection
        cf_detected, cf_msg = detect_cloudflare(resp)
        result['cloudflare_detected'] = cf_detected
        result['cloudflare_msg'] = cf_msg
        
        waf_detected, waf_signals = detect_waf(resp)
        result['waf_detected'] = waf_detected
        result['waf_signals'] = waf_signals
        
        captcha_detected, captcha_signals = detect_captcha(resp)
        result['captcha_detected'] = captcha_detected
        result['captcha_signals'] = captcha_signals
        
        sig_detected, sig_signals = detect_signature(resp)
        result['signature_detected'] = sig_detected
        result['signature_signals'] = sig_signals
        
        # DOM features
        dom_features = detect_dom_features(resp)
        result['dom_features'] = dom_features
        
        # Key selectors
        selectors = extract_key_selectors(resp)
        result['key_selectors'] = selectors
        
        # Anti-scrape difficulty assessment
        difficulty_score = 0
        if cf_detected:
            difficulty_score += 3
        if waf_detected:
            difficulty_score += 2
        if captcha_detected:
            difficulty_score += 3
        if sig_detected:
            difficulty_score += 1
        if resp.status_code == 403:
            difficulty_score += 3
        if dom_features.get('is_ssr'):
            difficulty_score -= 1  # SSR sites are easier
        result['difficulty_score'] = max(0, min(10, difficulty_score))
        
        if result['difficulty_score'] <= 2:
            result['difficulty_level'] = 'easy'
        elif result['difficulty_score'] <= 5:
            result['difficulty_level'] = 'medium'
        else:
            result['difficulty_level'] = 'hard'
        
        result['success'] = True
        
    except requests.exceptions.Timeout:
        result['success'] = False
        result['error'] = 'Timeout'
        result['difficulty_level'] = 'unknown'
        result['difficulty_score'] = -1
    except requests.exceptions.ConnectionError as e:
        result['success'] = False
        result['error'] = f'ConnectionError: {str(e)[:100]}'
        result['difficulty_level'] = 'unknown'
        result['difficulty_score'] = -1
    except Exception as e:
        result['success'] = False
        result['error'] = f'{type(e).__name__}: {str(e)[:100]}'
        result['difficulty_level'] = 'unknown'
        result['difficulty_score'] = -1
    
    return result


def main():
    print(f'Loading targets from: {TARGET_FILE}')
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Extract site entries (skip metadata keys)
    meta_keys = ['generated_at', 'total_targets', 'p0_count', 'p1_count', 'p2_count']
    sites = []
    for k, v in data.items():
        if k in meta_keys:
            continue
        if isinstance(v, dict) and 'url' in v:
            sites.append(v)
    
    print(f'Found {len(sites)} sites to probe')
    
    # Probe P0 sites first, then P1
    p0_sites = [s for s in sites if s.get('priority') == 'P0']
    p1_sites = [s for s in sites if s.get('priority') == 'P1']
    p2_sites = [s for s in sites if s.get('priority') == 'P2']
    
    print(f'P0: {len(p0_sites)}, P1: {len(p1_sites)}, P2: {len(p2_sites)}')
    
    results = []
    
    # Probe P0 sites
    for i, site in enumerate(p0_sites):
        name = site.get('name', 'unknown')
        url = site.get('url', '')
        priority = site.get('priority', 'P0')
        category = site.get('category', 'unknown')
        
        print(f'[{i+1}/{len(p0_sites)}] Probing P0: {name} ({url})')
        result = probe_site(name, url, priority, category)
        results.append(result)
        
        # Brief delay to avoid rate limiting
        time.sleep(0.5)
    
    # Probe P1 sites (sample up to 20)
    print(f'\nProbing P1 sites (first 20)...')
    for i, site in enumerate(p1_sites[:20]):
        name = site.get('name', 'unknown')
        url = site.get('url', '')
        priority = site.get('priority', 'P1')
        category = site.get('category', 'unknown')
        
        print(f'[{i+1}/20] Probing P1: {name} ({url})')
        result = probe_site(name, url, priority, category)
        results.append(result)
        time.sleep(0.5)
    
    # Save results
    output_file = os.path.join(OUTPUT_DIR, 'probe_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_probed': len(results),
            'successful': sum(1 for r in results if r.get('success')),
            'failed': sum(1 for r in results if not r.get('success')),
            'results': results,
        }, f, ensure_ascii=False, indent=2)
    
    print(f'\nResults saved to: {output_file}')
    print(f'Successful: {sum(1 for r in results if r.get("success"))}/{len(results)}')
    
    # Generate summary report
    generate_report(results, OUTPUT_DIR)


def generate_report(results, output_dir):
    """Generate a markdown report from probe results."""
    successful = [r for r in results if r.get('success')]
    failed = [r for r in results if not r.get('success')]
    
    # Difficulty distribution
    easy = [r for r in successful if r.get('difficulty_level') == 'easy']
    medium = [r for r in successful if r.get('difficulty_level') == 'medium']
    hard = [r for r in successful if r.get('difficulty_level') == 'hard']
    unknown = [r for r in successful if r.get('difficulty_level') == 'unknown']
    
    # Cloudflare detection
    cf_sites = [r for r in successful if r.get('cloudflare_detected')]
    
    # WAF detection
    waf_sites = [r for r in successful if r.get('waf_detected')]
    
    # CAPTCHA detection
    captcha_sites = [r for r in successful if r.get('captcha_detected')]
    
    # SPA vs SSR
    spa_sites = [r for r in successful if r.get('dom_features', {}).get('spa_framework_detected')]
    ssr_sites = [r for r in successful if r.get('dom_features', {}).get('is_ssr')]
    
    report_lines = []
    report_lines.append('# Site Profile Report')
    report_lines.append('')
    report_lines.append(f'**Generated**: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    report_lines.append(f'**Probed**: {len(results)} sites (P0 + top P1 samples)')
    report_lines.append(f'**Success**: {len(successful)} | **Failed**: {len(failed)}')
    report_lines.append('')
    
    # Summary statistics
    report_lines.append('## 1. Summary Statistics')
    report_lines.append('')
    report_lines.append('| Metric | Count |')
    report_lines.append('|--------|-------|')
    report_lines.append(f'| Total Probed | {len(results)} |')
    report_lines.append(f'| Successful | {len(successful)} |')
    report_lines.append(f'| Failed (timeout/connection) | {len(failed)} |')
    report_lines.append(f'| Difficulty: Easy | {len(easy)} |')
    report_lines.append(f'| Difficulty: Medium | {len(medium)} |')
    report_lines.append(f'| Difficulty: Hard | {len(hard)} |')
    report_lines.append(f'| Cloudflare Detected | {len(cf_sites)} |')
    report_lines.append(f'| WAF Detected | {len(waf_sites)} |')
    report_lines.append(f'| CAPTCHA Detected | {len(captcha_sites)} |')
    report_lines.append(f'| SPA Framework | {len(spa_sites)} |')
    report_lines.append(f'| SSR (Server-side) | {len(ssr_sites)} |')
    report_lines.append('')
    
    # Difficulty distribution chart (text-based)
    report_lines.append('## 2. Difficulty Distribution')
    report_lines.append('')
    total = len(successful) or 1
    report_lines.append('```')
    report_lines.append('Easy     [' + '=' * int(len(easy)/total*40) + '] ' + str(len(easy)))
    report_lines.append('Medium   [' + '=' * int(len(medium)/total*40) + '] ' + str(len(medium)))
    report_lines.append('Hard     [' + '=' * int(len(hard)/total*40) + '] ' + str(len(hard)))
    report_lines.append('```')
    report_lines.append('')
    
    # Cloudflare sites
    report_lines.append('## 3. Cloudflare Protected Sites')
    report_lines.append('')
    if cf_sites:
        report_lines.append('| Site | URL | Status | Message |')
        report_lines.append('|------|-----|--------|---------|')
        for r in cf_sites:
            report_lines.append(f'| {r["name"]} | {r["url"]} | {r["http_status"]} | {r.get("cloudflare_msg", "")} |')
    else:
        report_lines.append('No Cloudflare protection detected in sampled sites.')
    report_lines.append('')
    
    # CAPTCHA sites
    report_lines.append('## 4. Sites with CAPTCHA/Detection')
    report_lines.append('')
    if captcha_sites:
        report_lines.append('| Site | URL | Captcha Signals |')
        report_lines.append('|------|-----|-----------------|')
        for r in captcha_sites:
            signals = ', '.join(r.get('captcha_signals', []))
            report_lines.append(f'| {r["name"]} | {r["url"]} | {signals} |')
    else:
        report_lines.append('No CAPTCHA detected in sampled sites.')
    report_lines.append('')
    
    # WAF sites
    report_lines.append('## 5. WAF Protected Sites')
    report_lines.append('')
    if waf_sites:
        report_lines.append('| Site | URL | WAF Signals |')
        report_lines.append('|------|-----|-------------|')
        for r in waf_sites:
            signals = ', '.join(r.get('waf_signals', []))
            report_lines.append(f'| {r["name"]} | {r["url"]} | {signals} |')
    else:
        report_lines.append('No WAF detected in sampled sites.')
    report_lines.append('')
    
    # DOM features summary
    report_lines.append('## 6. DOM Structure Summary')
    report_lines.append('')
    report_lines.append('| Site | URL | Iframe Count | Shadow DOM | Scripts | SPA | SSR | JSON-LD |')
    report_lines.append('|------|-----|-------------|------------|---------|-----|-----|---------|')
    for r in successful[:30]:
        dom = r.get('dom_features', {})
        report_lines.append(
            f'| {r["name"]} | {r["url"]} | {dom.get("iframe_count", "?")} | '
            f'{dom.get("shadow_dom_elements", 0)} | {dom.get("script_count", "?")} | '
            f'{"Y" if dom.get("spa_framework_detected") else "N"} | '
            f'{"Y" if dom.get("is_ssr") else "N"} | '
            f'{"Y" if dom.get("has_jsonld") else "N"} |'
        )
    report_lines.append('')
    
    # Key selectors availability
    report_lines.append('## 7. Key Selectors Availability')
    report_lines.append('')
    report_lines.append('| Site | URL | Logo | Search Box | Main Content | Nav |')
    report_lines.append('|------|-----|------|------------|--------------|-----|')
    for r in successful[:30]:
        sel = r.get('key_selectors', {})
        logo = 'Y' if sel.get('logo') else 'N'
        search = 'Y' if sel.get('search_box') else 'N'
        main = 'Y' if sel.get('main_content') else 'N'
        nav = 'Y' if sel.get('navigation') else 'N'
        report_lines.append(f'| {r["name"]} | {r["url"]} | {logo} | {search} | {main} | {nav} |')
    report_lines.append('')
    
    # Per-site detailed results
    report_lines.append('## 8. Detailed Per-Site Profiles')
    report_lines.append('')
    for r in successful:
        report_lines.append(f'### {r["name"]} ({r["url"]})')
        report_lines.append('')
        report_lines.append('| Field | Value |')
        report_lines.append('|-------|-------|')
        report_lines.append(f'| Priority | {r.get("priority", "N/A")} |')
        report_lines.append(f'| Category | {r.get("category", "N/A")} |')
        report_lines.append(f'| HTTP Status | {r.get("http_status", "N/A")} |')
        report_lines.append(f'| Response Time | {r.get("response_time_ms", "N/A")} ms |')
        report_lines.append(f'| Content Length | {r.get("content_length", "N/A")} bytes |')
        report_lines.append(f'| Encoding | {r.get("encoding", "N/A")} |')
        report_lines.append(f'| Difficulty Score | {r.get("difficulty_score", "N/A")}/10 |')
        report_lines.append(f'| Difficulty Level | {r.get("difficulty_level", "N/A")} |')
        report_lines.append(f'| Cloudflare | {"YES" if r.get("cloudflare_detected") else "No"} |')
        report_lines.append(f'| WAF | {"YES" if r.get("waf_detected") else "No"} |')
        report_lines.append(f'| CAPTCHA | {"YES" if r.get("captcha_detected") else "No"} |')
        report_lines.append(f'| Signature | {"YES" if r.get("signature_detected") else "No"} |')
        dom = r.get('dom_features', {})
        report_lines.append(f'| SPA Detected | {"YES" if dom.get("spa_framework_detected") else "No"} |')
        report_lines.append(f'| SSR Detected | {"YES" if dom.get("is_ssr") else "No"} |')
        report_lines.append(f'| iframe Count | {dom.get("iframe_count", 0)} |')
        report_lines.append(f'| Shadow DOM | {dom.get("shadow_dom_elements", 0)} elements |')
        report_lines.append(f'| JSON-LD | {"YES" if dom.get("has_jsonld") else "No"} |')
        report_lines.append('')
    
    # Failed sites
    report_lines.append('## 9. Failed Sites')
    report_lines.append('')
    if failed:
        report_lines.append('| Site | URL | Error |')
        report_lines.append('|------|-----|-------|')
        for r in failed:
            report_lines.append(f'| {r["name"]} | {r["url"]} | {r.get("error", "unknown")} |')
    else:
        report_lines.append('All sites probed successfully.')
    report_lines.append('')
    
    # Recommendations
    report_lines.append('## 10. Recommendations for browser-cdp Skill')
    report_lines.append('')
    report_lines.append('### Easy Sites (score 0-2) — Ready for immediate integration:')
    easy_names = [r['name'] for r in easy]
    report_lines.append('- ' + '\n- '.join(easy_names[:15]) if easy_names else 'None found')
    report_lines.append('')
    
    report_lines.append('### Medium Sites (score 3-5) — Need adaptation strategies:')
    medium_names = [r['name'] for r in medium]
    report_lines.append('- ' + '\n- '.join(medium_names[:15]) if medium_names else 'None found')
    report_lines.append('')
    
    report_lines.append('### Hard Sites (score 6-10) — Require advanced anti-detection:')
    hard_names = [r['name'] for r in hard]
    report_lines.append('- ' + '\n- '.join(hard_names[:15]) if hard_names else 'None found')
    report_lines.append('')
    
    report_lines.append('### Strategy Recommendations:')
    report_lines.append('')
    report_lines.append('1. **Cloudflare-protected sites**: Use undetected-chromedriver or browser-stealth techniques')
    report_lines.append('2. **CAPTCHA sites**: Prioritize sites with low CAPTCHA risk or use solving services')
    report_lines.append('3. **SPA sites**: Ensure full page load before extraction (waitForSelector)')
    report_lines.append('4. **WAF sites**: Add header randomization and request throttling')
    report_lines.append('5. **Signature/encryption**: Reverse-engineer API endpoints or use CDP directly')
    report_lines.append('')
    
    # Write report
    report_path = os.path.join(output_dir, 'site_profile_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    print(f'Report saved to: {report_path}')


if __name__ == '__main__':
    main()
