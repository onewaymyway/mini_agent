"""
登录表单自动识别模块

支持：
- 自动检测页面登录表单
- 识别用户名/密码字段
- 识别登录按钮
- 支持多种登录表单布局
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class FormField:
    """表单字段信息"""
    selector: str
    field_type: str  # username, password, captcha, submit, etc.
    name: Optional[str] = None
    id: Optional[str] = None
    placeholder: Optional[str] = None
    required: bool = False
    
    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "type": self.field_type,
            "name": self.name,
            "id": self.id,
            "placeholder": self.placeholder,
            "required": self.required,
        }


@dataclass
class LoginForm:
    """登录表单信息"""
    selector: str
    fields: List[FormField] = field(default_factory=list)
    submit_selector: Optional[str] = None
    is_oauth: bool = False
    oauth_providers: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "fields": [f.to_dict() for f in self.fields],
            "submit_selector": self.submit_selector,
            "is_oauth": self.is_oauth,
            "oauth_providers": self.oauth_providers,
        }


class LoginFormDetector:
    """
    登录表单检测器
    
    自动识别页面中的登录表单，返回结构化信息。
    """
    
    # 登录表单选择器模式
    FORM_SELECTORS = [
        "form[action*='login']",
        "form[action*='signin']",
        "form[action*='auth']",
        "form[class*='login']",
        "form[class*='signin']",
        "form[id*='login']",
        "form[id*='signin']",
        "form[method='post'] input[type='password']",
        ".login-form",
        ".signin-form",
        "#login-form",
        "#signin-form",
        "[role='form'] input[type='password']",
    ]
    
    # 用户名字段选择器模式
    USERNAME_SELECTORS = [
        "input[name*='username']",
        "input[name*='user']",
        "input[name*='email']",
        "input[name*='login']",
        "input[name*='account']",
        "input[placeholder*='用户名']",
        "input[placeholder*='邮箱']",
        "input[placeholder*='手机']",
        "input[placeholder*='账号']",
        "input[type='text'][name*='user']",
        "input[type='email']",
    ]
    
    # 密码字段选择器模式
    PASSWORD_SELECTORS = [
        "input[name*='password']",
        "input[name*='pwd']",
        "input[placeholder*='密码']",
        "input[type='password']",
    ]
    
    # 验证码字段选择器模式
    CAPTCHA_SELECTORS = [
        "input[name*='captcha']",
        "input[name*='code']",
        "input[placeholder*='验证码']",
        "input[name*='verify']",
    ]
    
    # 登录按钮选择器模式
    SUBMIT_SELECTORS = [
        "button[type='submit']",
        "input[type='submit']",
        "button[class*='login']",
        "button[class*='submit']",
        "button[id*='login']",
        "button[text()='登录']",
        "button[text()='登录']",
        "a[class*='login']",
    ]
    
    # OAuth 提供商选择器模式
    OAUTH_SELECTORS = {
        "wechat": [
            "button[class*='wechat']",
            "a[class*='wechat']",
            "[class*='wx-login']",
            "button[text()='微信登录']",
        ],
        "alipay": [
            "button[class*='alipay']",
            "a[class*='alipay']",
            "[class*='ali-pay']",
            "button[text()='支付宝登录']",
        ],
        "weibo": [
            "button[class*='weibo']",
            "a[class*='weibo']",
            "button[text()='微博登录']",
        ],
        "qq": [
            "button[class*='qq']",
            "a[class*='qq']",
            "button[text()='QQ登录']",
        ],
        "github": [
            "button[class*='github']",
            "a[class*='github']",
            "button[text()='GitHub']",
        ],
        "google": [
            "button[class*='google']",
            "a[class*='google']",
            "button[text()='Google']",
        ],
    }
    
    def __init__(self, session):
        self.session = session
    
    def detect(self) -> Optional[LoginForm]:
        """
        检测页面登录表单
        
        Returns:
            LoginForm 对象，未检测到返回 None
        """
        # 尝试多种选择器
        for selector in self.FORM_SELECTORS:
            form = self._find_form(selector)
            if form:
                logger.info(f"检测到登录表单: {selector}")
                return self._analyze_form(form)
        
        # 回退：查找包含密码字段的表单
        form = self._find_form("form input[type='password']")
        if form:
            logger.info("通过密码字段检测到登录表单")
            return self._analyze_form(form)
        
        # 回退：查找包含登录按钮的表单
        form = self._find_form("form button:contains('登录')")
        if form:
            logger.info("通过登录按钮检测到登录表单")
            return self._analyze_form(form)
        
        logger.debug("未检测到登录表单")
        return None
    
    def _find_form(self, selector: str) -> Optional[str]:
        """查找表单选择器"""
        js = f'''
        (function() {{
            var forms = document.querySelectorAll("{selector}");
            if (forms.length > 0) {{
                return forms[0].tagName + (forms[0].id ? '#' + forms[0].id : '') + (forms[0].className ? '.' + forms[0].className.split(' ')[0] : '');
            }}
            return null;
        }})()
        '''
        try:
            result = self.session.eval_js(js)
            return result if result else None
        except Exception:
            return None
    
    def _analyze_form(self, form_selector: str) -> LoginForm:
        """分析表单结构"""
        js = f'''
        (function() {{
            var form = document.querySelector("{form_selector}");
            if (!form) return null;
            
            var result = {{
                selector: {form_selector!r},
                fields: [],
                submit: null,
                isOauth: false,
                oauthProviders: []
            }};
            
            // 分析字段
            var inputs = form.querySelectorAll('input');
            inputs.forEach(function(input) {{
                var type = input.type || 'text';
                var name = input.name || '';
                var id = input.id || '';
                var placeholder = input.placeholder || '';
                
                var field = {{
                    selector: '# ' + (id || '[name="' + name + '"]'),
                    type: 'text',
                    name: name,
                    placeholder: placeholder,
                    required: input.required
                }};
                
                if (type === 'password') {{
                    field.type = 'password';
                }} else if (type === 'checkbox' || type === 'radio') {{
                    field.type = type;
                }} else if (name.indexOf('captcha') !== -1 || name.indexOf('code') !== -1 || 
                           placeholder.indexOf('验证码') !== -1) {{
                    field.type = 'captcha';
                }} else if (name.indexOf('user') !== -1 || name.indexOf('email') !== -1 || 
                           name.indexOf('phone') !== -1 || name.indexOf('account') !== -1 ||
                           placeholder.indexOf('用户名') !== -1 || placeholder.indexOf('邮箱') !== -1 ||
                           placeholder.indexOf('手机') !== -1) {{
                    field.type = 'username';
                }}
                
                result.fields.push(field);
            }});
            
            // 查找提交按钮
            var submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
            if (submitBtn) {{
                result.submit = '# ' + (submitBtn.id || '[type="' + (submitBtn.type || 'submit') + '"]');
            }}
            
            // 检测 OAuth
            var oauthTypes = ['wechat', 'alipay', 'weibo', 'qq', 'github', 'google'];
            oauthTypes.forEach(function(provider) {{
                var selectors = document.querySelectorAll('[class*="' + provider + '"], [id*="' + provider + '"]');
                if (selectors.length > 0) {{
                    result.isOauth = true;
                    result.oauthProviders.push(provider);
                }}
            }});
            
            return result;
        }})()
        '''
        
        try:
            result = self.session.eval_js(js)
            if not result:
                return LoginForm(selector=form_selector)
            
            # 构建 LoginForm 对象
            login_form = LoginForm(
                selector=result.get("selector", form_selector),
                submit_selector=result.get("submit"),
                is_oauth=result.get("isOauth", False),
                oauth_providers=result.get("oauthProviders", []),
            )
            
            for field_data in result.get("fields", []):
                login_form.fields.append(FormField(
                    selector=field_data.get("selector", ""),
                    field_type=field_data.get("type", "text"),
                    name=field_data.get("name"),
                    placeholder=field_data.get("placeholder"),
                    required=field_data.get("required", False),
                ))
            
            return login_form
        except Exception as e:
            logger.error(f"分析表单失败: {e}")
            return LoginForm(selector=form_selector)
    
    def get_username_field(self) -> Optional[FormField]:
        """获取用户名字段"""
        form = self.detect()
        if form:
            for field in form.fields:
                if field.field_type == "username":
                    return field
        return None
    
    def get_password_field(self) -> Optional[FormField]:
        """获取密码字段"""
        form = self.detect()
        if form:
            for field in form.fields:
                if field.field_type == "password":
                    return field
        return None
    
    def get_captcha_field(self) -> Optional[FormField]:
        """获取验证码字段"""
        form = self.detect()
        if form:
            for field in form.fields:
                if field.field_type == "captcha":
                    return field
        return None
    
    def has_oauth(self) -> bool:
        """检查是否有 OAuth 登录选项"""
        form = self.detect()
        return form and form.is_oauth
    
    def get_oauth_providers(self) -> List[str]:
        """获取 OAuth 提供商列表"""
        form = self.detect()
        return form.oauth_providers if form else []


# 便捷函数
def detect_login_form(session) -> Optional[LoginForm]:
    """检测页面登录表单"""
    detector = LoginFormDetector(session)
    return detector.detect()


def get_login_fields(session) -> Dict[str, Optional[FormField]]:
    """获取登录表单关键字段"""
    detector = LoginFormDetector(session)
    return {
        "username": detector.get_username_field(),
        "password": detector.get_password_field(),
        "captcha": detector.get_captcha_field(),
    }
