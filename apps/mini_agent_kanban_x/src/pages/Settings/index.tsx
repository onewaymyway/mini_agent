import { Button, Card, Form, Input, message } from "antd";
import { useAuthStore } from "../../stores/authStore";
import { getHealth } from "../../api/endpoints";

export default function Settings() {
  const apiBase = useAuthStore((s) => s.apiBase);
  const token = useAuthStore((s) => s.token);
  const setApiBase = useAuthStore((s) => s.setApiBase);
  const setToken = useAuthStore((s) => s.setToken);

  const onFinish = async (values: { apiBase: string; token: string }) => {
    setApiBase(values.apiBase.trim());
    setToken(values.token.trim());
    try {
      await getHealth();
      message.success("已更新并连接成功");
    } catch (e) {
      message.error(`已保存，但连接测试失败：${(e as Error).message}`);
    }
  };

  return (
    <Card title="连接设置" style={{ maxWidth: 520 }}>
      <Form layout="vertical" initialValues={{ apiBase, token }} onFinish={onFinish}>
        <Form.Item name="apiBase" label="API Base" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="token" label="Token" rules={[{ required: true }]}>
          <Input.Password />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit">
            保存
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}
