import { Layout, Menu, Tag, Typography, Space, Button } from "antd";
import {
  DashboardOutlined,
  MessageOutlined,
  ClusterOutlined,
  SettingOutlined,
  LogoutOutlined,
} from "@ant-design/icons";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/authStore";
import { useUiStore } from "../stores/uiStore";
import { useStatus } from "../hooks/useStatus";

const { Header, Sider, Content } = Layout;

const items = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">总览</Link> },
  { key: "/chat", icon: <MessageOutlined />, label: <Link to="/chat">对话</Link> },
  { key: "/sessions", icon: <ClusterOutlined />, label: <Link to="/sessions">会话</Link> },
  { key: "/settings", icon: <SettingOutlined />, label: <Link to="/settings">设置</Link> },
];

export default function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const collapsed = useUiStore((s) => s.collapsed);
  const setCollapsed = useUiStore((s) => s.setCollapsed);
  const clearToken = useAuthStore((s) => s.clear);
  const { data: status } = useStatus();

  const selectedKey =
    items.find((i) => i.key !== "/" && location.pathname.startsWith(i.key))?.key || "/";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}>
        <div style={{ height: 48, margin: 12, color: "#fff", fontWeight: 600, textAlign: "center" }}>
          {collapsed ? "MA" : "Mini Agent 看板"}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} items={items} />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", padding: "0 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Typography.Text strong>
            {status?.state ? <Tag color="processing">状态: {String(status.state)}</Tag> : <Tag>连接中…</Tag>}
            {status?.model ? <Tag>模型: {String(status.model)}</Tag> : null}
          </Typography.Text>
          <Space>
            <Button
              icon={<LogoutOutlined />}
              onClick={() => {
                clearToken();
                navigate("/login");
              }}
            >
              退出登录
            </Button>
          </Space>
        </Header>
        <Content style={{ margin: 16 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
