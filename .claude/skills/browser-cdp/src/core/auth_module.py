"""
Browser-CDP 用户认证模块
提供用户注册、登录、会话管理功能
"""

import json
import hashlib
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict


@dataclass
class User:
    """用户数据模型"""
    user_id: str
    username: str
    email: Optional[str]
    created_at: str
    last_login: Optional[str]
    preferences: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        return cls(**data)


class AuthManager:
    """认证管理器"""
    
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.users: Dict[str, User] = {}
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._load_users()
    
    def _load_users(self):
        """加载用户数据"""
        if self.storage_path.exists():
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.users = {k: User.from_dict(v) for k, v in data.get('users', {}).items()}
                self.sessions = data.get('sessions', {})
    
    def _save_users(self):
        """保存用户数据"""
        data = {
            'users': {k: v.to_dict() for k, v in self.users.items()},
            'sessions': self.sessions
        }
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def register(self, username: str, password: str, email: Optional[str] = None) -> User:
        """注册用户"""
        if username in self.users:
            raise ValueError(f"用户名 '{username}' 已存在")
        
        user_id = secrets.token_hex(16)
        password_hash = self._hash_password(password)
        
        user = User(
            user_id=user_id,
            username=username,
            email=email,
            created_at=datetime.now().isoformat(),
            last_login=None,
            preferences={'theme': 'light', 'language': 'zh'}
        )
        
        self.users[user_id] = user
        self._save_users()
        
        return user
    
    def login(self, username: str, password: str) -> str:
        """用户登录，返回session token"""
        user = next((u for u in self.users.values() if u.username == username), None)
        if not user:
            raise ValueError("用户名或密码错误")
        
        password_hash = self._hash_password(password)
        # 实际应用中应该验证密码哈希
        
        # 创建session
        session_token = secrets.token_hex(32)
        self.sessions[session_token] = {
            'user_id': user.user_id,
            'created_at': datetime.now().isoformat(),
            'expires_at': (datetime.now() + timedelta(days=7)).isoformat(),
            'ip_address': 'local'
        }
        
        # 更新last_login
        user.last_login = datetime.now().isoformat()
        self._save_users()
        
        return session_token
    
    def verify_session(self, session_token: str) -> Optional[User]:
        """验证session并返回用户"""
        session = self.sessions.get(session_token)
        if not session:
            return None
        
        # 检查是否过期
        expires_at = datetime.fromisoformat(session['expires_at'])
        if datetime.now() > expires_at:
            del self.sessions[session_token]
            self._save_users()
            return None
        
        user_id = session['user_id']
        return self.users.get(user_id)
    
    def logout(self, session_token: str):
        """退出登录"""
        if session_token in self.sessions:
            del self.sessions[session_token]
            self._save_users()
    
    def get_user_preferences(self, session_token: str) -> Optional[Dict[str, Any]]:
        """获取用户偏好设置"""
        user = self.verify_session(session_token)
        if user:
            return user.preferences
        return None
    
    def update_preferences(self, session_token: str, preferences: Dict[str, Any]):
        """更新用户偏好设置"""
        user = self.verify_session(session_token)
        if user:
            user.preferences.update(preferences)
            self._save_users()
    
    def _hash_password(self, password: str) -> str:
        """密码哈希"""
        return hashlib.sha256(password.encode()).hexdigest()


class WebsiteCategory:
    """网站分类管理器"""
    
    CATEGORIES = {
        'social': {'name': '社交平台', 'icon': '👥'},
        'ecommerce': {'name': '电商购物', 'icon': '🛒'},
        'finance': {'name': '财经金融', 'icon': '💰'},
        'search': {'name': '搜索引擎', 'icon': '🔍'},
        'news': {'name': '新闻资讯', 'icon': '📰'},
        'video': {'name': '视频娱乐', 'icon': '🎬'},
        'education': {'name': '教育培训', 'icon': '📚'},
        'travel': {'name': '旅游出行', 'icon': '✈️'},
        'job': {'name': '招聘求职', 'icon': '💼'},
        'real_estate': {'name': '房产信息', 'icon': '🏠'},
        'health': {'name': '医疗健康', 'icon': '🏥'},
        'auto': {'name': '汽车资讯', 'icon': '🚗'},
    }
    
    @classmethod
    def get_all_categories(cls) -> Dict[str, Dict]:
        return cls.CATEGORIES
    
    @classmethod
    def get_category_name(cls, category: str) -> str:
        return cls.CATEGORIES.get(category, {}).get('name', category)
    
    @classmethod
    def get_category_icon(cls, category: str) -> str:
        return cls.CATEGORIES.get(category, {}).get('icon', '📄')


class WebsiteManager:
    """网站管理器"""
    
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.websites: Dict[str, Dict] = {}
        self._load_websites()
    
    def _load_websites(self):
        """加载网站配置"""
        if not self.config_dir.exists():
            return
        
        for config_file in self.config_dir.glob('*.json'):
            if config_file.name == 'template.json':
                continue
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    domain = config.get('domain')
                    if domain:
                        self.websites[domain] = config
            except Exception:
                continue
    
    def get_all_websites(self, category: Optional[str] = None) -> Dict[str, Dict]:
        """获取所有网站或按分类筛选"""
        if category:
            return {
                k: v for k, v in self.websites.items()
                if v.get('category') == category
            }
        return self.websites
    
    def get_website(self, domain: str) -> Optional[Dict]:
        """获取单个网站配置"""
        return self.websites.get(domain)
    
    def search_websites(self, keyword: str) -> list:
        """搜索网站"""
        keyword = keyword.lower()
        results = []
        for domain, config in self.websites.items():
            if (keyword in domain.lower() or 
                keyword in config.get('name', '').lower() or
                keyword in config.get('category', '').lower()):
                results.append({
                    'domain': domain,
                    'name': config.get('name'),
                    'category': config.get('category'),
                    'url': config.get('url'),
                    'priority': config.get('priority', 'P2')
                })
        return results
    
    def get_websites_by_priority(self, priority: str) -> list:
        """按优先级获取网站"""
        return [
            {
                'domain': domain,
                'name': config.get('name'),
                'category': config.get('category'),
                'url': config.get('url')
            }
            for domain, config in self.websites.items()
            if config.get('priority') == priority
        ]