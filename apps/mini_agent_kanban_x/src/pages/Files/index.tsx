import { useState } from "react";
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Col,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import {
  DeleteOutlined,
  DownloadOutlined,
  FileOutlined,
  FolderOutlined,
  FolderAddOutlined,
  ReloadOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import { useFsActions, useFsList, useFsRead } from "../../hooks/useFiles";
import { apiBaseUrl } from "../../api/client";
import { fsDownloadUrl } from "../../api/endpoints";

/**
 * 文件浏览页面：对应旧看板里分散在"工作流运行面板文件选择器"等处的 fs/* 能力，
 * 这里做成一个独立通用页面（方案文档 P3），后续其它页面可以直接跳转过来。
 */
export default function Files() {
  const [dir, setDir] = useState("");
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");

  const list = useFsList(dir);
  const file = useFsRead(selected);
  const { write, mkdir, remove } = useFsActions(dir);
  const [editContent, setEditContent] = useState<string | null>(null);

  const crumbs = ["(root)", ...dir.split("/").filter(Boolean)];

  const openDir = (path: string) => {
    setDir(path);
    setSelected(undefined);
    setEditContent(null);
  };

  const openFile = (path: string) => {
    setSelected(path);
    setEditContent(null);
  };

  return (
    <Row gutter={16}>
      <Col span={9}>
        <Card
          title="目录"
          size="small"
          extra={
            <Space>
              <Button size="small" icon={<ReloadOutlined />} onClick={() => list.refetch()} />
              <Button size="small" icon={<FolderAddOutlined />} onClick={() => setNewFolderOpen(true)} />
            </Space>
          }
        >
          <Breadcrumb
            style={{ marginBottom: 8 }}
            items={crumbs.map((c, idx) => ({
              title: <a onClick={() => openDir(idx === 0 ? "" : crumbs.slice(1, idx + 1).join("/"))}>{c}</a>,
            }))}
          />
          {list.isError && <Alert type="error" showIcon message={(list.error as Error).message} />}
          <List
            loading={list.isLoading}
            size="small"
            dataSource={list.data?.entries || []}
            locale={{ emptyText: <Empty description="空目录" /> }}
            renderItem={(entry) => (
              <List.Item
                actions={[
                  <Button
                    key="dl"
                    size="small"
                    type="text"
                    icon={<DownloadOutlined />}
                    href={entry.is_dir ? undefined : `${apiBaseUrl()}${fsDownloadUrl(entry.path)}`}
                    disabled={entry.is_dir}
                    target="_blank"
                  />,
                  <Popconfirm
                    key="del"
                    title={`确认删除 ${entry.name}？`}
                    onConfirm={() => remove.mutate(entry.path, { onSuccess: () => message.success("已删除") })}
                  >
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>,
                ]}
              >
                <a onClick={() => (entry.is_dir ? openDir(entry.path) : openFile(entry.path))}>
                  {entry.is_dir ? <FolderOutlined /> : <FileOutlined />} {entry.name}
                </a>
                {!entry.is_dir && entry.size !== undefined && <Tag style={{ marginLeft: 8 }}>{entry.size} B</Tag>}
              </List.Item>
            )}
          />
        </Card>
      </Col>
      <Col span={15}>
        <Card
          title={selected || "选择一个文件查看"}
          size="small"
          extra={
            selected &&
            editContent !== null && (
              <Button
                size="small"
                type="primary"
                icon={<SaveOutlined />}
                loading={write.isPending}
                onClick={() =>
                  write.mutate({ path: selected, content: editContent }, { onSuccess: () => message.success("已保存") })
                }
              >
                保存
              </Button>
            )
          }
        >
          {!selected ? (
            <Empty description="从左侧选择文件" />
          ) : file.isLoading ? (
            <Typography.Text type="secondary">加载中…</Typography.Text>
          ) : file.isError ? (
            <Alert type="warning" showIcon message="该文件可能不是文本格式，或读取失败" />
          ) : (
            <Input.TextArea
              value={editContent ?? file.data?.content ?? ""}
              onChange={(e) => setEditContent(e.target.value)}
              autoSize={{ minRows: 16, maxRows: 28 }}
              style={{ fontFamily: "monospace", fontSize: 12 }}
            />
          )}
        </Card>
      </Col>

      <Modal
        title="新建文件夹"
        open={newFolderOpen}
        onCancel={() => setNewFolderOpen(false)}
        onOk={() => {
          const path = dir ? `${dir}/${newFolderName}` : newFolderName;
          mkdir.mutate(path, {
            onSuccess: () => {
              message.success("已创建");
              setNewFolderOpen(false);
              setNewFolderName("");
            },
          });
        }}
        confirmLoading={mkdir.isPending}
      >
        <Input placeholder="文件夹名称" value={newFolderName} onChange={(e) => setNewFolderName(e.target.value)} />
      </Modal>
    </Row>
  );
}
