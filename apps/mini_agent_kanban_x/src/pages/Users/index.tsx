import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useUsers } from "../../hooks/useUsers";
import type { UserInfo } from "../../api/types";

const { Title, Text, Paragraph } = Typography;

const ROLES = ["family", "colleague", "agent", "public"]; // owner 不可通过 UI 创建/修改

function fmtTs(ts?: number) {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

export default function Users() {
  const { users, create, update, remove, rotateToken } = useUsers();
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [newTokenInfo, setNewTokenInfo] = useState<{ user_id: string; token: string } | null>(null);

  const notEnabled =
    (users.error as any)?.status === 404 ||
    /Multi-user mode not enabled/i.test((users.error as any)?.message || "");

  const columns = [
    { title: "用户 ID", dataIndex: "user_id" },
    { title: "名称", dataIndex: "name" },
    {
      title: "角色",
      dataIndex: "role",
      render: (v: string) => <Tag color={v === "owner" ? "gold" : "blue"}>{v}</Tag>,
    },
    { title: "信任等级", dataIndex: "trust_level" },
    { title: "创建时间", dataIndex: "created_at", render: fmtTs },
    { title: "最近活跃", dataIndex: "last_seen", render: fmtTs },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, u: UserInfo) =>
        u.role === "owner" ? (
          <Text type="secondary">owner 不可通过此处修改</Text>
        ) : (
          <>
            <Select
              size="small"
              value={u.role}
              style={{ width: 110, marginRight: 8 }}
              options={ROLES.map((r) => ({ label: r, value: r }))}
              onChange={(role) =>
                update.mutate(
                  { userId: u.user_id, body: { role } },
                  {
                    onSuccess: (r) => (r.ok ? message.success("角色已更新") : message.error(r.message || "更新失败")),
                    onError: (e: any) => message.error(e?.message || "更新失败"),
                  }
                )
              }
            />
            <Button
              size="small"
              style={{ marginRight: 8 }}
              loading={rotateToken.isPending}
              onClick={() =>
                rotateToken.mutate(u.user_id, {
                  onSuccess: (r) => {
                    if (r.ok && r.token) setNewTokenInfo({ user_id: u.user_id, token: r.token });
                    else message.error(r.message || "重置失败");
                  },
                  onError: (e: any) => message.error(e?.message || "重置失败"),
                })
              }
            >
              重置 Token
            </Button>
            <Popconfirm
              title="确认删除该用户？"
              onConfirm={() =>
                remove.mutate(u.user_id, {
                  onSuccess: (r) => (r.ok ? message.success("已删除") : message.error(r.message || "删除失败")),
                  onError: (e: any) => message.error(e?.message || "删除失败"),
                })
              }
            >
              <Button size="small" danger loading={remove.isPending}>
                删除
              </Button>
            </Popconfirm>
          </>
        ),
    },
  ];

  return (
    <div>
      <Title level={4}>👤 用户管理</Title>

      {notEnabled ? (
        <Alert
          type="info"
          showIcon
          message="多用户模式未开启"
          description="daemon 需要以 --http-multi-user 启动才能使用用户管理功能；单用户部署下本页面不可用。"
        />
      ) : (
        <Card
          extra={
            <>
              <Button icon={<ReloadOutlined />} style={{ marginRight: 8 }} onClick={() => users.refetch()}>
                刷新
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                新建用户
              </Button>
            </>
          }
        >
          <Table
            rowKey="user_id"
            size="small"
            loading={users.isLoading}
            dataSource={users.data?.users || []}
            columns={columns as any}
            pagination={false}
            locale={{ emptyText: <Empty description="暂无用户" /> }}
          />
        </Card>
      )}

      <Modal
        title="新建用户"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ role: "colleague", trust_level: 5 }}
          onFinish={(values) =>
            create.mutate(values, {
              onSuccess: (r) => {
                if (r.ok) {
                  message.success("已创建用户");
                  setCreateOpen(false);
                  form.resetFields();
                  if (r.token && r.user_id) setNewTokenInfo({ user_id: r.user_id, token: r.token });
                } else {
                  message.error(r.message || "创建失败");
                }
              },
              onError: (e: any) => message.error(e?.message || "创建失败"),
            })
          }
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={ROLES.map((r) => ({ label: r, value: r }))} />
          </Form.Item>
          <Form.Item name="trust_level" label="信任等级">
            <InputNumber min={0} max={10} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新 Token（仅显示一次，请立即复制保存）"
        open={!!newTokenInfo}
        onCancel={() => setNewTokenInfo(null)}
        footer={
          <Button type="primary" onClick={() => setNewTokenInfo(null)}>
            我已保存
          </Button>
        }
      >
        {newTokenInfo && (
          <>
            <Paragraph>
              <Text strong>用户：</Text> {newTokenInfo.user_id}
            </Paragraph>
            <Paragraph copyable={{ text: newTokenInfo.token }} code>
              {newTokenInfo.token}
            </Paragraph>
            <Alert type="warning" showIcon message="离开此弹窗后将无法再次查看该 Token 明文，只能重新生成一个新的" />
          </>
        )}
      </Modal>
    </div>
  );
}
