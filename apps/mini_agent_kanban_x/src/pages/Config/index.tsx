import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { SaveOutlined, SearchOutlined } from "@ant-design/icons";
import { useConfig } from "../../hooks/useConfig";
import type { ConfigFieldRow } from "../../api/types";

const { Title, Text } = Typography;

function renderControl(
  field: ConfigFieldRow,
  value: unknown,
  onChange: (v: unknown) => void
) {
  if (field.sensitive) {
    return <Tag>{field.value ? "已配置" : "未配置"}（不可在此修改）</Tag>;
  }
  switch (field.type) {
    case "bool":
      return <Switch checked={!!value} onChange={onChange} />;
    case "int":
      return (
        <InputNumber
          style={{ width: 200 }}
          value={value as number}
          precision={0}
          onChange={(v) => onChange(v)}
        />
      );
    case "float":
      return (
        <InputNumber style={{ width: 200 }} value={value as number} onChange={(v) => onChange(v)} />
      );
    case "str":
    default:
      // 一部分字符串字段实际是有限枚举（如 log_level），后端未区分，这里统一用
      // 文本输入，保证任意 str 字段都可编辑，不因为猜错枚举而挡住用户。
      return (
        <Input style={{ width: 320 }} value={value as string} onChange={(e) => onChange(e.target.value)} />
      );
  }
}

export default function Config() {
  const { config, patch } = useConfig();
  const [drafts, setDrafts] = useState<Record<string, unknown>>({});
  const [keyword, setKeyword] = useState("");

  // 后端每次成功 PATCH 后返回最新 categories，这里把 drafts 重置为服务端权威值，
  // 避免本地草稿和服务端出现漂移。
  useEffect(() => {
    setDrafts({});
  }, [config.data?.config_path]);

  const categories = config.data?.categories || [];

  const filtered = useMemo(() => {
    if (!keyword.trim()) return categories;
    const kw = keyword.trim().toLowerCase();
    return categories
      .map((cat) => ({
        ...cat,
        fields: cat.fields.filter(
          (f) => f.json_key.toLowerCase().includes(kw) || f.label.toLowerCase().includes(kw)
        ),
      }))
      .filter((cat) => cat.fields.length > 0);
  }, [categories, keyword]);

  const dirtyCount = Object.keys(drafts).length;

  const onSave = () => {
    const updates = Object.entries(drafts).map(([json_key, value]) => ({ json_key, value }));
    if (updates.length === 0) return;
    patch.mutate(updates, {
      onSuccess: () => {
        message.success("已保存，部分配置需要重启 agent 进程才能生效");
        setDrafts({});
      },
      onError: (e: any) => message.error(e?.message || "保存失败"),
    });
  };

  return (
    <div>
      <Title level={4}>⚙️ 配置管理</Title>
      {config.data?.restart_required && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="上次保存已生效于展示，但多数配置需要重启 agent 进程才会真正生效"
        />
      )}

      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          allowClear
          placeholder="按字段名 / 中文说明过滤"
          prefix={<SearchOutlined />}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 280 }}
        />
        <Button
          type="primary"
          icon={<SaveOutlined />}
          disabled={dirtyCount === 0}
          loading={patch.isPending}
          onClick={onSave}
        >
          保存修改{dirtyCount > 0 ? `（${dirtyCount}）` : ""}
        </Button>
        {config.data?.config_path && <Text type="secondary">{config.data.config_path}</Text>}
      </Space>

      {filtered.length === 0 ? (
        <Empty description="暂无匹配的配置项" />
      ) : (
        <Collapse
          items={filtered.map((cat) => ({
            key: cat.id,
            label: (
              <>
                {cat.icon ? `${cat.icon} ` : ""}
                {cat.label}
                <Tag style={{ marginLeft: 8 }}>{cat.fields.length} 项</Tag>
              </>
            ),
            children: (
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                {cat.fields.map((f) => {
                  const draftKey = f.json_key;
                  const hasDraft = draftKey in drafts;
                  const currentValue = hasDraft ? drafts[draftKey] : f.value;
                  return (
                    <div
                      key={f.json_key}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        borderBottom: "1px solid #f0f0f0",
                        paddingBottom: 8,
                        flexWrap: "wrap",
                        gap: 8,
                      }}
                    >
                      <div>
                        <Space>
                          <Text strong>{f.label}</Text>
                          <Text type="secondary" code>
                            {f.json_key}
                          </Text>
                          {f.customized && <Tag color="blue">已自定义</Tag>}
                          {hasDraft && <Tag color="orange">未保存</Tag>}
                        </Space>
                        <div>
                          <Tooltip title="默认值">
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              默认: {f.sensitive ? "—" : JSON.stringify(f.default)}
                            </Text>
                          </Tooltip>
                        </div>
                      </div>
                      {renderControl(f, currentValue, (v) =>
                        setDrafts((prev) => ({ ...prev, [draftKey]: v }))
                      )}
                    </div>
                  );
                })}
              </Space>
            ),
          }))}
        />
      )}
    </div>
  );
}
