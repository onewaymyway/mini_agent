#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
淘宝商品搜索原型 - 演示电商核心功能

功能：
1. 商品展示（搜索/列表/详情）
2. 购物车操作（添加/查看/结算）
3. 订单流程（下单/状态追踪）

基于 browser-cdp skill 架构规范实现
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

# 添加 skill 路径
SKILL_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, SKILL_ROOT)

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型定义（符合架构规范）
# ============================================================================

class ProductStatus(Enum):
    """商品状态枚举"""
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PRE_SALE = "pre_sale"


class OrderStatus(Enum):
    """订单状态枚举"""
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Product:
    """商品数据模型"""
    # 基本信息
    id: str = ""
    title: str = ""
    price: float = 0.0
    original_price: float = 0.0
    image: str = ""
    shop: str = ""
    location: str = ""
    
    # 销售信息
    sales: int = 0
    rating: float = 0.0
    reviews: int = 0
    
    # 状态信息
    status: ProductStatus = ProductStatus.IN_STOCK
    in_stock: bool = True
    
    # 元数据
    source_domain: str = ""
    url: str = ""
    scraped_at: str = ""
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        status_val = self.status.value if isinstance(self.status, ProductStatus) else self.status
        return {
            "id": self.id,
            "title": self.title,
            "price": self.price,
            "original_price": self.original_price,
            "image": self.image,
            "shop": self.shop,
            "location": self.location,
            "sales": self.sales,
            "rating": self.rating,
            "reviews": self.reviews,
            "status": status_val,
            "in_stock": self.in_stock,
            "source_domain": self.source_domain,
            "url": self.url,
            "scraped_at": self.scraped_at or datetime.now().isoformat(),
            "tags": self.tags,
        }


@dataclass
class CartItem:
    """购物车项"""
    product: Product
    quantity: int = 1
    
    @property
    def subtotal(self) -> float:
        return self.product.price * self.quantity
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "product": self.product.to_dict(),
            "quantity": self.quantity,
            "subtotal": self.subtotal,
        }


@dataclass
class Cart:
    """购物车"""
    items: List[CartItem] = field(default_factory=list)
    
    @property
    def total_items(self) -> int:
        return sum(item.quantity for item in self.items)
    
    @property
    def total_amount(self) -> float:
        return sum(item.subtotal for item in self.items)
    
    def add(self, product: Product, quantity: int = 1) -> bool:
        """添加商品到购物车"""
        for item in self.items:
            if item.product.id == product.id:
                item.quantity += quantity
                return True
        self.items.append(CartItem(product=product, quantity=quantity))
        return True
    
    def remove(self, product_id: str) -> bool:
        """从购物车移除商品"""
        self.items = [item for item in self.items if item.product.id != product_id]
        return True
    
    def update_quantity(self, product_id: str, quantity: int) -> bool:
        """更新商品数量"""
        for item in self.items:
            if item.product.id == product_id:
                item.quantity = max(1, quantity)
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_items": self.total_items,
            "total_amount": self.total_amount,
            "updated_at": datetime.now().isoformat(),
        }


@dataclass
class Order:
    """订单数据模型"""
    order_id: str = ""
    items: List[CartItem] = field(default_factory=list)
    total_amount: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        status_val = self.status.value if isinstance(self.status, OrderStatus) else self.status
        return {
            "order_id": self.order_id,
            "items": [item.to_dict() for item in self.items],
            "total_amount": self.total_amount,
            "status": status_val,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ============================================================================
# 配置文件加载器（符合架构规范）
# ============================================================================

class ConfigLoader:
    """网站配置加载器"""
    
    def __init__(self, config_dir: str = None):
        self.config_dir = config_dir or os.path.join(os.path.dirname(__file__), '..', 'config', 'websites')
        self.config_dir = os.path.normpath(self.config_dir)
        self._cache: Dict[str, Dict] = {}
    
    def load(self, domain: str) -> Optional[Dict]:
        """加载网站配置"""
        if domain in self._cache:
            return self._cache[domain]
        
        config_file = os.path.join(self.config_dir, f"{domain}.json")
        if not os.path.exists(config_file):
            logger.warning(f"Config not found: {config_file}")
            return None
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self._cache[domain] = config
            return config
        except Exception as e:
            logger.error(f"Failed to load config {config_file}: {e}")
            return None
    
    def list_configs(self) -> List[str]:
        """列出所有已配置的网站"""
        configs = []
        for f in os.listdir(self.config_dir):
            if f.endswith('.json') and f not in ['template.json', 'example.com.json']:
                domain = f.replace('.json', '')
                configs.append(domain)
        return sorted(configs)


# ============================================================================
# 商品解析器（符合架构规范）
# ============================================================================

class EcommerceParser:
    """电商商品解析器基类"""
    
    def __init__(self, domain: str, config: Dict):
        self.domain = domain
        self.config = config
        self.selectors = config.get('custom_config', {})
    
    def parse_list_page(self, html: str) -> List[Product]:
        """解析商品列表页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        products = []
        
        # 尝试多种选择器
        item_selectors = [
            self.selectors.get('result_item', '.gl-item'),
            '.item', '.product', '.goods-card', '.goods-item'
        ]
        
        items = None
        for sel in item_selectors:
            items = soup.select(sel)
            if items:
                break
        
        if not items:
            return products
        
        for item in items[:20]:
            product = self._parse_item(item)
            if product:
                products.append(product)
        
        return products
    
    def _parse_item(self, item) -> Optional[Product]:
        """解析单个商品项"""
        from bs4 import BeautifulSoup
        
        # 标题
        title_sel = self.selectors.get('title', '.p-name em, .item-title')
        title_el = item.select_one(title_sel) if isinstance(item, BeautifulSoup) else item.find(text=True, recursive=False)
        title = title_el.string.strip() if title_el else ""
        
        # 价格
        price_sel = self.selectors.get('price', '.p-price strong i, .price')
        price_el = item.select_one(price_sel) if isinstance(item, BeautifulSoup) else None
        price_text = price_el.string.strip() if price_el else ""
        
        # 提取价格数字
        import re
        match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
        price = float(match.group()) if match else 0.0
        
        # 销量
        sales_sel = self.selectors.get('sales', '.deal-count, .sales')
        sales_el = item.select_one(sales_sel) if isinstance(item, BeautifulSoup) else None
        sales_text = sales_el.string.strip() if sales_el else ""
        
        # 店铺
        shop_sel = self.selectors.get('shop', '.shop-name, .seller')
        shop_el = item.select_one(shop_sel) if isinstance(item, BeautifulSoup) else None
        shop = shop_el.string.strip() if shop_el else ""
        
        # URL
        url = ""
        link_el = item.find('a', href=True)
        if link_el and link_el.get('href'):
            url = link_el['href']
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('/'):
                url = f'https://{self.domain}{url}'
        
        # 图片
        img_sel = self.selectors.get('image', 'img')
        img_el = item.select_one(img_sel) if isinstance(item, BeautifulSoup) else None
        image = ""
        if img_el:
            image = img_el.get('src') or img_el.get('data-src') or ""
        
        return Product(
            id=self._generate_id(title),
            title=title,
            price=price,
            sales=sales_text,
            shop=shop,
            url=url,
            image=image,
            source_domain=self.domain,
            scraped_at=datetime.now().isoformat(),
        )
    
    def _generate_id(self, title: str) -> str:
        """生成商品ID"""
        import hashlib
        return hashlib.md5(title.encode()).hexdigest()[:12]


# ============================================================================
# 购物车服务
# ============================================================================

class CartService:
    """购物车服务"""
    
    def __init__(self):
        self._cart = Cart()
        self._history: List[Dict] = []
    
    def add_to_cart(self, product: Product, quantity: int = 1) -> bool:
        """添加商品到购物车"""
        result = self._cart.add(product, quantity)
        if result:
            self._history.append({
                "action": "add",
                "product_id": product.id,
                "quantity": quantity,
                "timestamp": datetime.now().isoformat(),
            })
        return result
    
    def remove_from_cart(self, product_id: str) -> bool:
        """从购物车移除商品"""
        result = self._cart.remove(product_id)
        if result:
            self._history.append({
                "action": "remove",
                "product_id": product_id,
                "timestamp": datetime.now().isoformat(),
            })
        return result
    
    def view_cart(self) -> Dict:
        """查看购物车内容"""
        return self._cart.to_dict()
    
    def clear_cart(self) -> bool:
        """清空购物车"""
        self._cart.items.clear()
        return True


# ============================================================================
# 订单服务
# ============================================================================

class OrderService:
    """订单服务"""
    
    def __init__(self):
        self._orders: Dict[str, Order] = {}
        self._order_counter = 0
    
    def create_order(self, cart: Cart) -> Optional[Order]:
        """创建订单"""
        if not cart.items:
            return None
        
        self._order_counter += 1
        order_id = f"ORD{datetime.now().strftime('%Y%m%d')}{self._order_counter:06d}"
        
        order = Order(
            order_id=order_id,
            items=cart.items.copy(),
            total_amount=cart.total_amount,
            status=OrderStatus.PENDING,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        
        self._orders[order_id] = order
        return order
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """获取订单详情"""
        return self._orders.get(order_id)
    
    def pay_order(self, order_id: str) -> bool:
        """支付订单"""
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.PAID
            order.updated_at = datetime.now().isoformat()
            return True
        return False
    
    def ship_order(self, order_id: str) -> bool:
        """发货"""
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.PAID:
            order.status = OrderStatus.SHIPPED
            order.updated_at = datetime.now().isoformat()
            return True
        return False
    
    def list_orders(self) -> List[Dict]:
        """列出所有订单"""
        return [o.to_dict() for o in self._orders.values()]


# ============================================================================
# 电商原型主控制器
# ============================================================================

class EcommercePrototype:
    """电商原型主控制器 - 整合所有模块"""
    
    def __init__(self):
        self.config_loader = ConfigLoader()
        self.cart_service = CartService()
        self.order_service = OrderService()
        self._products: List[Product] = []
        self._search_history: List[Dict] = []
    
    # ========================================================================
    # 核心功能：商品展示
    # ========================================================================
    
    def search_products(self, query: str, domain: str = "jd.com", max_results: int = 20) -> List[Product]:
        """搜索商品"""
        logger.info(f"Searching products: '{query}' on {domain}")
        
        # 模拟搜索结果（实际应调用 browser-cdp）
        products = self._mock_search(query, domain, max_results)
        
        self._search_history.append({
            "query": query,
            "domain": domain,
            "count": len(products),
            "timestamp": datetime.now().isoformat(),
        })
        
        return products
    
    def get_product_detail(self, product_id: str) -> Optional[Product]:
        """获取商品详情"""
        for p in self._products:
            if p.id == product_id:
                return p
        return None
    
    def list_products(self, domain: str = None, category: str = None) -> List[Product]:
        """列出商品"""
        if domain:
            return [p for p in self._products if p.source_domain == domain]
        return self._products
    
    # ========================================================================
    # 核心功能：购物车
    # ========================================================================
    
    def add_to_cart(self, product: Product, quantity: int = 1) -> bool:
        """添加商品到购物车"""
        return self.cart_service.add_to_cart(product, quantity)
    
    def view_cart(self) -> Dict:
        """查看购物车"""
        return self.cart_service.view_cart()
    
    def checkout(self) -> Optional[Order]:
        """结算"""
        cart = self.cart_service.view_cart()
        if cart['total_items'] == 0:
            return None
        
        # 创建临时购物车用于下单
        temp_cart = Cart()
        for item in cart['items']:
            prod = Product(**item['product'])
            temp_cart.add(prod, item['quantity'])
        
        order = self.order_service.create_order(temp_cart)
        if order:
            self.cart_service.clear_cart()
        return order
    
    # ========================================================================
    # 核心功能：订单流程
    # ========================================================================
    
    def pay_order(self, order_id: str) -> bool:
        """支付订单"""
        return self.order_service.pay_order(order_id)
    
    def ship_order(self, order_id: str) -> bool:
        """发货"""
        return self.order_service.ship_order(order_id)
    
    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """获取订单状态"""
        order = self.order_service.get_order(order_id)
        if order:
            return order.to_dict()
        return None
    
    def list_orders(self) -> List[Dict]:
        """列出订单"""
        return self.order_service.list_orders()
    
    # ========================================================================
    # 辅助方法
    # ========================================================================
    
    def _mock_search(self, query: str, domain: str, max_results: int) -> List[Product]:
        """模拟搜索结果（用于演示）"""
        # 根据域名生成模拟数据
        mock_data = {
            "jd.com": {
                "template": "{keyword} 京东自营 正品保障",
                "shops": ["京东自营", "品牌旗舰店", "专卖店"],
                "locations": ["北京", "上海", "广州"],
            },
            "taobao.com": {
                "template": "{keyword} 淘宝 全网精选",
                "shops": ["淘宝店", "天猫店", "工厂店"],
                "locations": ["杭州", "广州", "深圳"],
            },
            "pinduoyun.com": {
                "template": "{keyword} 拼团价 限时优惠",
                "shops": ["品牌店", "厂家店", "农场直发"],
                "locations": ["义乌", "广州", "深圳"],
            },
        }
        
        data = mock_data.get(domain, mock_data["jd.com"])
        products = []
        
        for i in range(min(max_results, 10)):
            product = Product(
                id=f"{domain}_{i:04d}",
                title=data["template"].replace("{keyword}", query) + f" #{i+1}",
                price=round(9.9 + i * 10.5, 2),
                original_price=round(19.9 + i * 15.0, 2),
                shop=data["shops"][i % len(data["shops"])],
                location=data["locations"][i % len(data["locations"])],
                sales=f"{1000 + i * 500}+人付款",
                rating=round(4.5 + i * 0.1, 1),
                reviews=500 + i * 100,
                source_domain=domain,
                url=f"https://{domain}/product/{i}",
                scraped_at=datetime.now().isoformat(),
                tags=[query, domain, "热门"],
            )
            products.append(product)
        
        self._products.extend(products)
        return products
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "total_products": len(self._products),
            "total_searches": len(self._search_history),
            "total_orders": len(self.order_service._orders),
            "cart_items": self.cart_service._cart.total_items,
            "cart_amount": self.cart_service._cart.total_amount,
            "configured_domains": self.config_loader.list_configs(),
        }


# ============================================================================
# 主入口
# ============================================================================

def main():
    """主函数 - 演示电商原型功能"""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    prototype = EcommercePrototype()
    
    print("=" * 60)
    print("  browser-cdp 电商网站原型演示")
    print("=" * 60)
    
    # 1. 显示已配置网站
    print("\n【已配置网站】")
    configs = prototype.config_loader.list_configs()
    for domain in configs[:10]:  # 只显示前10个
        config = prototype.config_loader.load(domain)
        if config:
            name = config.get('name', domain)
            priority = config.get('priority', 'P?')
            category = config.get('category', '')
            print(f"  [{priority}] {name} ({domain}) - {category}")
    
    # 2. 搜索商品
    print("\n【搜索商品】")
    query = "蓝牙耳机"
    products = prototype.search_products(query, "jd.com", max_results=5)
    print(f"  搜索 '{query}' 找到 {len(products)} 个结果")
    for p in products[:3]:
        print(f"    - {p.title[:40]}...")
        print(f"      价格: ¥{p.price} | 店铺: {p.shop} | 销量: {p.sales}")
    
    # 3. 添加到购物车
    print("\n【添加商品到购物车】")
    if products:
        prototype.add_to_cart(products[0], quantity=2)
        prototype.add_to_cart(products[1], quantity=1)
        cart = prototype.view_cart()
        print(f"  购物车: {cart['total_items']} 件商品, 总计 ¥{cart['total_amount']:.2f}")
        for item in cart['items']:
            print(f"    - {item['product']['title'][:30]}... x{item['quantity']} = ¥{item['subtotal']:.2f}")
    
    # 4. 结算下单
    print("\n【结算下单】")
    order = prototype.checkout()
    if order:
        print(f"  订单创建成功!")
        print(f"    订单号: {order.order_id}")
        print(f"    金额: ¥{order.total_amount:.2f}")
        print(f"    状态: {order.status.value}")
    
    # 5. 订单状态追踪
    print("\n【订单状态追踪】")
    if order:
        status = prototype.get_order_status(order.order_id)
        print(f"  订单 {order.order_id} 当前状态: {status['status']}")
    
    # 6. 统计信息
    print("\n【系统统计】")
    stats = prototype.get_stats()
    print(f"  已配置网站: {len(stats['configured_domains'])} 个")
    print(f"  搜索次数: {stats['total_searches']}")
    print(f"  订单数量: {stats['total_orders']}")
    
    print("\n" + "=" * 60)
    print("  演示完成!")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
