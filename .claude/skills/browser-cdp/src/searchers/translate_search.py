#!/usr/bin/env python
"""
translate_search.py - 翻译服务搜索器

使用 browser-cdp skill 访问在线翻译服务，支持多语言翻译查询。

用法:
    python translate_search.py --text "Hello world" --source en --target zh
    python translate_search.py --text "你好世界" --source zh --target en
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import (
    random_delay, get_random_ua, save_results, clean_text, truncate_text
)
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 翻译服务配置 ==========
TRANSLATE_SERVICES = {
    "google": {
        "name": "Google Translate",
        "url": "https://translate.google.com",
        "api_url": "https://translate.google.com/translate_a/single?client=gtx&sl={source}&tl={target}&dt=t&q={text}"
    },
    "deepl": {
        "name": "DeepL",
        "url": "https://www.deepl.com/translator",
        "api_url": None  # DeepL 需要 API key
    },
    "bing": {
        "name": "Bing Translate",
        "url": "https://www.bing.com/translator",
        "api_url": None
    }
}

# 语言代码映射
LANG_CODES = {
    "en": "en", "zh": "zh-CN", "zh-cn": "zh-CN", "zh-tw": "zh-TW",
    "ja": "ja", "ko": "ko", "fr": "fr", "de": "de",
    "es": "es", "ru": "ru", "pt": "pt", "it": "it",
    "nl": "nl", "pl": "pl", "tr": "tr", "ar": "ar",
    "hi": "hi", "th": "th", "vi": "vi", "id": "id"
}


class TranslationSearcher(BaseSearcher):
    """翻译服务搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "translate"
        self._extra_param = ""
        self._service = "google"  # google/deepl/bing
    
    @property
    def source_name(self) -> str:
        return "translation"
    
    @property
    def supported_types(self) -> List[str]:
        return ["translate", "detect", "batch"]
    
    @property
    def requires_login(self) -> bool:
        return False
    
    @property
    def rate_limit(self) -> float:
        return 1.0
    
    def translate(self, text: str, source: str = "auto", target: str = "en",
                  service: str = "google", port: int = 9333, **kwargs) -> Dict:
        """翻译文本"""
        self._service = service
        
        # 标准化语言代码
        src_code = LANG_CODES.get(source.lower(), source.lower())
        tgt_code = LANG_CODES.get(target.lower(), target.lower())
        
        if service == "google":
            return self._translate_google(text, src_code, tgt_code, port, **kwargs)
        elif service == "bing":
            return self._translate_bing(text, src_code, tgt_code, port, **kwargs)
        else:
            return self._translate_google(text, src_code, tgt_code, port, **kwargs)
    
    def _translate_google(self, text: str, source: str, target: str,
                          port: int, **kwargs) -> Dict:
        """使用 Google Translate"""
        url = f"https://translate.google.com/?sl={source}&tl={target}&text={quote(text)}&op=translate"
        
        js_code = '''
(function() {
    var result = {
        translated_text: '',
        source_text: '',
        source_lang: '',
        target_lang: '',
        source: 'google_translate'
    };
    
    // 尝试从页面提取翻译结果
    var transEl = document.querySelector('.tlid-translation, [data-result-text]');
    if (transEl) {
        result.translated_text = transEl.textContent.trim();
    }
    
    // 备用：从 URL 参数提取
    var urlParams = new URLSearchParams(window.location.search);
    var text = urlParams.get('text');
    if (text) {
        result.source_text = decodeURIComponent(text);
    }
    
    var sl = urlParams.get('sl');
    var tl = urlParams.get('tl');
    if (sl) result.source_lang = sl;
    if (tl) result.target_lang = tl;
    
    return result;
})()
        '''
        
        try:
            response = run_cmd('navigate', url=url, port=port)
            time.sleep(random.uniform(2, 4))
            data = run_cmd('evaluate', js=js_code, port=port)
            
            if data and 'result' in data:
                return data['result']
        except Exception as e:
            self.logger.error(f"Google Translate failed: {e}")
        
        return {
            'translated_text': '',
            'source_text': text,
            'source_lang': source,
            'target_lang': target,
            'source': 'google_translate',
            'error': str(e) if 'e' in locals() else None
        }
    
    def _translate_bing(self, text: str, source: str, target: str,
                        port: int, **kwargs) -> Dict:
        """使用 Bing Translate"""
        url = f"https://www.bing.com/translator?from={source}&to={target}&text={quote(text)}"
        
        js_code = '''
(function() {
    var result = {
        translated_text: '',
        source_text: '',
        source_lang: '',
        target_lang: '',
        source: 'bing_translate'
    };
    
    // 尝试从页面提取翻译结果
    var transEl = document.querySelector('.trans_result, [data-testid="translated-text"]');
    if (transEl) {
        result.translated_text = transEl.textContent.trim();
    }
    
    return result;
})()
        '''
        
        try:
            response = run_cmd('navigate', url=url, port=port)
            time.sleep(random.uniform(2, 4))
            data = run_cmd('evaluate', js=js_code, port=port)
            
            if data and 'result' in data:
                return data['result']
        except Exception as e:
            self.logger.error(f"Bing Translate failed: {e}")
        
        return {
            'translated_text': '',
            'source_text': text,
            'source_lang': source,
            'target_lang': target,
            'source': 'bing_translate',
            'error': str(e) if 'e' in locals() else None
        }
    
    def detect_language(self, text: str, port: int = 9333, **kwargs) -> Dict:
        """检测语言"""
        # 使用 Google Translate 的检测功能
        url = f"https://translate.google.com/?sl=auto&tl=en&text={quote(text)}&op=translate"
        
        js_code = '''
(function() {
    var result = {
        detected_lang: '',
        confidence: '',
        source: 'google_translate'
    };
    
    // 尝试从页面提取检测到的语言
    var langEl = document.querySelector('.detect-lang, [data-lang]');
    if (langEl) {
        result.detected_lang = langEl.textContent.trim();
    }
    
    return result;
})()
        '''
        
        try:
            response = run_cmd('navigate', url=url, port=port)
            time.sleep(random.uniform(2, 3))
            data = run_cmd('evaluate', js=js_code, port=port)
            
            if data and 'result' in data:
                return data['result']
        except Exception as e:
            self.logger.error(f"Language detection failed: {e}")
        
        return {
            'detected_lang': '',
            'confidence': '',
            'source': 'google_translate',
            'error': str(e) if 'e' in locals() else None
        }
    
    def batch_translate(self, texts: List[str], source: str = "auto", 
                       target: str = "en", service: str = "google",
                       port: int = 9333, **kwargs) -> List[Dict]:
        """批量翻译"""
        results = []
        for i, text in enumerate(texts):
            result = self.translate(text, source, target, service, port, **kwargs)
            result['original_text'] = text
            result['index'] = i
            results.append(result)
            
            # 添加延迟避免限流
            if i < len(texts) - 1:
                time.sleep(random.uniform(1, 2))
        
        return results
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            import requests
            resp = requests.get("https://translate.google.com", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False
    
    def search(self, query: str, search_type: str = "translate",
               max_results: int = 10, language: str = "en",
               port: int = 9333, **kwargs) -> List[Dict]:
        """搜索/翻译（兼容 BaseSearcher 接口）"""
        if search_type == "translate":
            target = kwargs.get('target', language)
            source = kwargs.get('source', 'auto')
            result = self.translate(query, source, target, port=port, **kwargs)
            return [result]
        elif search_type == "detect":
            result = self.detect_language(query, port=port, **kwargs)
            return [result]
        elif search_type == "batch":
            texts = query.split('\n') if '\n' in query else [query]
            return self.batch_translate(texts, kwargs.get('source', 'auto'), 
                                       kwargs.get('target', language), port=port, **kwargs)
        else:
            return []


# ========== 命令行接口 ==========
def main():
    parser = argparse.ArgumentParser(description='翻译服务搜索器')
    parser.add_argument('--text', '-t', required=True, help='要翻译的文本')
    parser.add_argument('--source', '-s', default='auto', help='源语言 (默认: auto)')
    parser.add_argument('--target', '-tg', default='en', help='目标语言 (默认: en)')
    parser.add_argument('--service', '-sv', default='google', 
                       choices=['google', 'bing'],
                       help='翻译服务 (默认: google)')
    parser.add_argument('--detect', '-d', action='store_true',
                       help='检测语言而非翻译')
    parser.add_argument('--batch', '-b', help='批量翻译文件（每行一个文本）')
    parser.add_argument('--output', '-o', help='输出文件路径')
    parser.add_argument('--port', '-p', type=int, default=9333, help='浏览器端口')
    
    args = parser.parse_args()
    
    searcher = TranslationSearcher()
    
    if args.batch:
        # 批量翻译
        with open(args.batch, 'r', encoding='utf-8') as f:
            texts = [line.strip() for line in f if line.strip()]
        
        results = searcher.batch_translate(
            texts, args.source, args.target, args.service, args.port
        )
        
        if args.output:
            save_results(results, args.output)
            print(f"Results saved to {args.output}")
        else:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        
        print(f"\nTranslated {len(results)} texts")
    elif args.detect:
        # 检测语言
        result = searcher.detect_language(args.text, args.port)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # 单条翻译
        result = searcher.translate(
            args.text, args.source, args.target, args.service, args.port
        )
        
        if args.output:
            save_results([result], args.output)
            print(f"Result saved to {args.output}")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        
        if result.get('translated_text'):
            print(f"\n翻译结果: {result['translated_text']}")


if __name__ == "__main__":
    main()
