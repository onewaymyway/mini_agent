import { Layout, Menu, Tag, Typography, Space, Button, Badge, Drawer, List, Alert } from "antd";
import {
  DashboardOutlined,
  MessageOutlined,
  ClusterOutlined,
  SettingOutlined,
  LogoutOutlined,
  FolderOutlined,
  PictureOutlined,
  BulbOutlined,
  BellOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  FlagOutlined,
  ApartmentOutlined,
  RiseOutlined,
  ReadOutlined,
  ExperimentOutlined,
  ClockCircleOutlined,
  CalendarOutlined,
  ApiOutlined,
  NotificationOutlined,
  ToolOutlined,
  ControlOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/authStore";
import { useUiStore } from "../stores/uiStore";
import { useStatus } from "../hooks/useStatus";
import { usePendingApprovals, useTopbarModules } from "../hooks/usePermissions";

const { Header, Sider, Content } = Layout;

const items = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">总览</Link> },
  { key: "/chat", icon: <MessageOutlined />, label: <Link to="/chat">对话</Link> },
  { key: "/sessions", icon: <ClusterOutlined />, label: <Link to="/sessions">会话</Link> },
  { key: "/goals", icon: <FlagOutlined />, label: <Link to="/goals">目标看板</Link> },
  { key: "/workflows", icon: <ApartmentOutlined />, label: <Link to="/workflows">工作流</Link> },
  { key: "/growth", icon: <RiseOutlined />, label: <Link to="/growth">成长顾问</Link> },
  { key: "/capability", icon: <ReadOutlined />, label: <Link to="/capability">能力学习</Link> },
  { key: "/evolution", icon: <ExperimentOutlined />, label: <Link to="/evolution">进化提案</Link> },
  { key: "/cron", icon: <ClockCircleOutlined />, label: <Link to="/cron">Cron 任务</Link> },
  { key: "/schedule", icon: <CalendarOutlined />, label: <Link to="/schedule">全局日程</Link> },
  { key: "/external-input", icon: <ApiOutlined />, label: <Link to="/external-input">外部输入网关</Link> },
  { key: "/watchlist", icon: <NotificationOutlined />, label: <Link to="/watchlist">关注与通知</Link> },
  { key: "/hybrid-exec", icon: <ToolOutlined />, label: <Link to="/hybrid-exec">混合执行</Link> },
  { key: "/config", icon: <ControlOutlined />, label: <Link to="/config">配置管理</Link> },
  { key: "/users", icon: <TeamOutlined />, label: <Link to="/users">用户管理</Link> },
  { key: "/files", icon: <FolderOutlined />, label: <Link to="/files">文件</Link> },
  { key: "/artifacts", icon: <PictureOutlined />, label: <Link to="/artifacts">产出物</Link> },
  { key: "/self", icon: <BulbOutlined />, label: <Link to="/self">自我状态</Link> },
  { key: "/settings", icon: <SettingOutlined />, label: <Link to="/settings">设置</Link> },
];

export default function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const collapsed = useUiStore((s) => s.collapsed);
  const setCollapsed = useUiStore((s) => s.setCollapsed);
  const clearToken = useAuthStore((s) => s.clear);
  const { data: status } = useStatus();
  const { permissions, interactions } = usePendingApprovals();
  const { autonomous, sentinel, inbox, pause, resume } = useTopbarModules();
  const [inboxOpen, setInboxOpen] = useState(false);

  const selectedKey =
    items.find((i) => i.key !== "/" && location.pathname.startsWith(i.key))?.key || "/";
  const pendingTotal = permissions.length + interactions.length;
  const sentinelTotal = sentinel.data?.total ?? sentinel.data?.items?.length ?? 0;
  const inboxTotal = inbox.data?.items?.length ?? 0;

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}>
        <div style={{ height: 48, margin: 12, color: "#fff", fontWeight: 600, textAlign: "center" }}>
          {collapsed ? "MA" : "Mini Agent 看板"}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} items={items} />
      </Sider>
      <Layout>
        <Header
          style={{
            background: "#fff",
            padding: "0 16px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            height: "auto",
            minHeight: 64,
          }}
        >
          <Space wrap>
            {status?.state ? <Tag color="processing">状态: {String(status.state)}</Tag> : <Tag>连接中…</Tag>}
            {status?.model ? <Tag>模型: {String(status.model)}</Tag> : null}
            {autonomous.data?.queue_depth ? <Tag color="orange">排队: {autonomous.data.queue_depth}</Tag> : null}
            {pendingTotal > 0 && (
              <Tag color="red" onClick={() => navigate("/chat")} style={{ cursor: "pointer" }}>
                待处理请求: {pendingTotal}
              </Tag>
            )}
            {sentinelTotal > 0 && <Tag color="volcano">哨兵异常: {sentinelTotal}</Tag>}
          </Space>
          <Space>
            <Badge count={inboxTotal} size="small">
              <Button icon={<BellOutlined />} onClick={() => setInboxOpen(true)}>
                待办
              </Button>
            </Badge>
            {autonomous.data?.scheduling_paused ? (
              <Button icon={<PlayCircleOutlined />} loading={resume.isPending} onClick={() => resume.mutate()}>
                恢复调度
              </Button>
            ) : (
              <Button icon={<PauseCircleOutlined />} loading={pause.isPending} onClick={() => pause.mutate()}>
                暂停调度
              </Button>
            )}
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

      <Drawer title="全局待办中心" open={inboxOpen} onClose={() => setInboxOpen(false)} width={420}>
        {sentinelTotal > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`系统状态哨兵：发现 ${sentinelTotal} 项可能需要留意`}
            description={
              <List
                size="small"
                dataSource={sentinel.data?.items || []}
                renderItem={(it) => (
                  <List.Item>
                    <Typography.Text>{it.title || it.detail}</Typography.Text>
                  </List.Item>
                )}
              />
            }
          />
        )}
        <List
          header="跨会话待办"
          dataSource={inbox.data?.items || []}
          locale={{ emptyText: "暂无待办" }}
          renderItem={(it) => (
            <List.Item>
              <Typography.Text strong>{it.title}</Typography.Text>
              <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
                {it.summary}
              </Typography.Paragraph>
            </List.Item>
          )}
        />
      </Drawer>
    </Layout>
  );
}
