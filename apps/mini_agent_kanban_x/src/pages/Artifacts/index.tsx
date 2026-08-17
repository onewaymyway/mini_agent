import { useState } from "react";
import { Alert, Card, Col, Empty, Image, List, Row, Tag, Typography } from "antd";
import { useArtifactDetail, useArtifactsList } from "../../hooks/useArtifacts";
import { apiBaseUrl } from "../../api/client";
import { artifactFileUrl } from "../../api/endpoints";

const IMAGE_EXT = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"];

/** 产出物浏览 + 预览页面：合并旧看板的"📁 产出物浏览"与"🖼️ 产出预览"两个 Tab。 */
export default function Artifacts() {
  const [manifestId, setManifestId] = useState<string | undefined>(undefined);
  const list = useArtifactsList();
  const detail = useArtifactDetail(manifestId);

  return (
    <Row gutter={16}>
      <Col span={9}>
        <Card title="产出物清单" size="small">
          {list.isError && <Alert type="error" showIcon message={(list.error as Error).message} />}
          <List
            loading={list.isLoading}
            dataSource={list.data?.manifests || []}
            locale={{ emptyText: <Empty description="暂无产出物" /> }}
            renderItem={(m) => (
              <List.Item onClick={() => setManifestId(m.manifest_id)} style={{ cursor: "pointer" }}>
                <Typography.Text strong>{m.title || m.manifest_id}</Typography.Text>
                <div>
                  {m.session_id && <Tag>session: {m.session_id}</Tag>}
                  {m.goal_id && <Tag color="gold">goal: {m.goal_id}</Tag>}
                </div>
              </List.Item>
            )}
          />
        </Card>
      </Col>
      <Col span={15}>
        <Card title={manifestId ? `预览：${manifestId}` : "选择一个产出物"} size="small">
          {!manifestId ? (
            <Empty description="从左侧选择" />
          ) : detail.isLoading ? (
            <Typography.Text type="secondary">加载中…</Typography.Text>
          ) : (
            <List
              dataSource={detail.data?.files || []}
              renderItem={(f) => {
                const isImage = IMAGE_EXT.some((ext) => f.name.toLowerCase().endsWith(ext));
                const url = `${apiBaseUrl()}${artifactFileUrl(manifestId, f.path)}`;
                return (
                  <List.Item>
                    <div style={{ width: "100%" }}>
                      <Typography.Text>{f.name}</Typography.Text>
                      {isImage ? (
                        <div style={{ marginTop: 8 }}>
                          <Image src={url} width={240} />
                        </div>
                      ) : (
                        <a href={url} target="_blank" rel="noreferrer">
                          下载 / 查看
                        </a>
                      )}
                    </div>
                  </List.Item>
                );
              }}
            />
          )}
        </Card>
      </Col>
    </Row>
  );
}
