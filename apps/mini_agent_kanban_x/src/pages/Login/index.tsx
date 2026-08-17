import { useState } from "react";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../../stores/authStore";
import { getHealth, getWhoami } from "../../api/endpoints";

/**
 * 简化版登录页：
 *  - 对应旧看板 --auto-token / 手动粘贴 Token 的场景：直接填 API Base + Token；
 *  - 服务端如果开启了账户登录门禁（对应旧 auth.py --require-login），
 *    这里的 Token 输入框可以直接填从 /v1/users 拿到的账户 Token；
 *    是否强制账户名+密码登录取决于后端部署形态，SPA 侧始终以 Token 为唯一凭证，
 *    逻辑更简单也更符合"前后端分离"的鉴权惯例。
 */
export default function Login() {
  const navigate = useNavigate();
  const setToken = useAuthStore((s) => s.setToken);
  const setApiBase = useAuthStore((s) => s.setApiBase);
  const apiBase = useAuthStore((s) => s.apiBase);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { apiBase: string; token: string }) => {
    setLoading(true);
    try {
      setApiBase(values.apiBase.trim());
      setToken(values.token.trim());
      await getHealth();
      try {
        await getWhoami();
      } catch {
        // whoami 在某些部署形态下可能不可用，不阻塞登录
      }
      message.success("连接成功");
      navigate("/");
    } catch (e) {
      message.error(`连接失败：${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center", background: "#f5f5f5" }}>
      <Card style={{ width: 420 }}>
        <Typography.Title level={4} style={{ textAlign: "center" }}>
          Mini Agent 看板
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center" }}>
          请输入 mini-agent daemon 的 API Base 与 Token
        </Typography.Paragraph>
        <Form layout="vertical" initialValues={{ apiBase }} onFinish={onFinish}>
          <Form.Item name="apiBase" label="API Base" rules={[{ required: true }]}>
            <Input placeholder="/v1 或 http://127.0.0.1:8765/v1" />
          </Form.Item>
          <Form.Item name="token" label="Token" rules={[{ required: true }]}>
            <Input.Password placeholder="来自 .agent/agent_api.key 或用户 Token" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              连接
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
