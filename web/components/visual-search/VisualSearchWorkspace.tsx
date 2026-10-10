"use client";

import { App, Alert, Button, Card, Empty, Flex, Image, InputNumber, Modal, Space, Tag, Typography, Upload } from "antd";
import { InboxOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
import ProductImage from "@/components/inventory/ProductImage";
import RegionSelector from "./RegionSelector";
import { getVisualStatus, rebuildVisual, saveVisualFeedback, searchVisual, type CropRegion, type VisualResult, type VisualStatus } from "@/lib/api/visual-search";
import { getApiBaseUrl } from "@/lib/api/client";

interface SearchTask {
  key: string;
  region?: CropRegion;
  result?: VisualResult;
  error?: string;
  confirmed?: number | "none";
}
interface Photo {
  id: string;
  file: File;
  url: string;
  aspect: number;
  tasks: SearchTask[];
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

export default function VisualSearchWorkspace() {
  const { message } = App.useApp();
  const [photos, setPhotos] = useState<Photo[]>([]);
  const urls = useRef<string[]>([]);
  const photoCount = useRef(0);
  const [photoId, setPhotoId] = useState("");
  const [taskKey, setTaskKey] = useState("whole");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [status, setStatus] = useState<VisualStatus>();
  const [statusError, setStatusError] = useState("");
  const [indexOpen, setIndexOpen] = useState(false);
  const [indexBusy, setIndexBusy] = useState(false);
  const [limit, setLimit] = useState(10);
  const [plan, setPlan] = useState<Awaited<ReturnType<typeof rebuildVisual>>>();
  const [paidConfirm, setPaidConfirm] = useState(false);
  const current = photos.find((p) => p.id === photoId);
  const task = current?.tasks.find((t) => t.key === taskKey);
  const regions = current?.tasks.filter((t) => t.region).map((t) => t.region!) ?? [];
  const selectedRegion = task?.region ? regions.indexOf(task.region) : -1;

  async function refresh() {
    try { setStatus(await getVisualStatus()); setStatusError(""); }
    catch (error) { setStatusError(errorText(error)); }
  }
  useEffect(() => {
    let active = true;
    getVisualStatus().then((value) => { if (active) { setStatus(value); setStatusError(""); } }).catch((error) => { if (active) setStatusError(errorText(error)); });
    return () => { active = false; };
  }, []);
  useEffect(() => () => { urls.current.forEach((url) => URL.revokeObjectURL(url)); }, []);
  const running = status?.jobs.some((job) => job.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => { getVisualStatus().then(setStatus).catch((error) => setStatusError(errorText(error))); }, 5000);
    return () => clearInterval(timer);
  }, [running]);

  function updateTask(id: string, key: string, patch: Partial<SearchTask>) {
    setPhotos((old) => old.map((p) => p.id === id ? { ...p, tasks: p.tasks.map((t) => t.key === key ? { ...t, ...patch } : t) } : p));
  }
  async function execute(tasks: SearchTask[]) {
    if (!current || busyRef.current) return;
    const target = current;
    busyRef.current = true; setBusy(true);
    try {
      // Sequential calls give each region its own query ID and avoid paid concurrency spikes.
      for (const item of tasks) {
        setTaskKey(item.key);
        updateTask(target.id, item.key, { error: undefined, result: undefined, confirmed: undefined });
        try { updateTask(target.id, item.key, { result: await searchVisual(target.file, item.region) }); }
        catch (error) { updateTask(target.id, item.key, { error: errorText(error) }); }
      }
      await refresh();
    } finally { busyRef.current = false; setBusy(false); }
  }
  async function feedback(productId: number | null) {
    if (!current || !task?.result || busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try {
      await saveVisualFeedback(task.result.query_id, productId);
      updateTask(current.id, task.key, { confirmed: productId ?? "none" });
      message.success("人工确认已保存");
    } catch (error) { message.error(errorText(error)); }
    finally { busyRef.current = false; setBusy(false); }
  }
  function addRegion(region: CropRegion) {
    if (!current || busy) return;
    const key = crypto.randomUUID();
    setPhotos((old) => old.map((p) => p.id === current.id ? { ...p, tasks: [...p.tasks, { key, region }] } : p));
    setTaskKey(key);
  }
  async function previewIndex() {
    setIndexBusy(true);
    try { setPlan(await rebuildVisual(true, limit)); }
    catch (error) { message.error(errorText(error)); }
    finally { setIndexBusy(false); }
  }
  async function startIndex() {
    setIndexBusy(true);
    try {
      const result = await rebuildVisual(false, limit);
      message.success(`索引任务已创建：${result.job_id}`);
      setPaidConfirm(false); setIndexOpen(false); setPlan(undefined);
      await refresh();
    } catch (error) { message.error(errorText(error)); }
    finally { setIndexBusy(false); }
  }
  return <div className="visual-workspace">
    <PageHeading title="商品视觉搜索" meta={status ? `可搜索 ${status.indexed} 款` : undefined}
      actions={<Space><Button icon={<ReloadOutlined />} onClick={refresh}>刷新状态</Button><Button onClick={() => setIndexOpen(true)}>图库索引</Button></Space>} />
    <Typography.Paragraph type="secondary">上传照片查找相似商品。多款商品可手动框选后分别搜索，结果需由你核对。</Typography.Paragraph>
    {statusError && <Alert type="error" showIcon title={statusError} description={<span>本地首次使用：<a href={`${getApiBaseUrl()}/api/visual-search/index/status`} target="_blank" rel="noreferrer">打开接口并登录管理员账号</a>，再刷新状态。</span>} />}
    {status && (!status.schema_ready || !status.api_key_configured) && <Alert type="warning" showIcon title={!status.schema_ready ? "视觉索引尚未初始化" : "尚未配置百炼 API Key"} description="可以上传和框选图片；管理员完成配置及图库索引后可执行真实搜索。" />}
    <Upload.Dragger accept="image/jpeg,image/png,image/webp" multiple disabled={busy} showUploadList={false}
      beforeUpload={async (file) => {
        if (file.size > 10 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) { message.error("请选择 10MB 以内的 JPG、PNG 或 WEBP 图片"); return false; }
        if (photoCount.current >= 20) { message.error("最多上传 20 张图片，请先清空照片"); return false; }
        photoCount.current += 1;
        let bitmap: ImageBitmap;
        try { bitmap = await createImageBitmap(file, { imageOrientation: "from-image" }); }
        catch { photoCount.current -= 1; message.error("无法读取图片，请重新选择"); return false; }
        if (bitmap.width * bitmap.height > 40_000_000) { bitmap.close(); photoCount.current -= 1; message.error("图片分辨率过大，请缩小后上传"); return false; }
        const aspect = bitmap.width / bitmap.height;
        bitmap.close();
        const id = crypto.randomUUID(), url = URL.createObjectURL(file);
        urls.current.push(url);
        setPhotos((old) => [...old, { id, file, url, aspect, tasks: [{ key: "whole" }] }]);
        setPhotoId(id); setTaskKey("whole");
        return false;
      }}>
      <p className="ant-upload-drag-icon"><InboxOutlined /></p>
      <p className="ant-upload-text">点击或拖入商品照片，可一次选择多张</p>
      <p className="ant-upload-hint">JPG、PNG、WEBP，每张不超过 10MB，最多 20 张</p>
    </Upload.Dragger>
    {photos.length > 0 && <Flex wrap gap={8} className="visual-photo-tabs">{photos.map((photo, i) => <Button key={photo.id} disabled={busy} type={photo.id === photoId ? "primary" : "default"} onClick={() => { setPhotoId(photo.id); setTaskKey("whole"); }}>照片 {i + 1} · {photo.file.name}</Button>)}
      <Button disabled={busy} danger onClick={() => { urls.current.forEach((url) => URL.revokeObjectURL(url)); urls.current = []; photoCount.current = 0; setPhotos([]); setPhotoId(""); }}>清空照片</Button>
    </Flex>}
    {!current ? <Empty description="选择照片后，直接搜索整图或框选商品区域" /> : <div className="visual-search-columns">
      <Card title="原图与商品区域">
        <RegionSelector key={current.id} src={current.url} regions={regions} selected={selectedRegion} disabled={busy} onAdd={addRegion} onSelect={(i) => setTaskKey(current.tasks.filter((t) => t.region)[i].key)} />
        <Typography.Paragraph type="secondary">在商品周围拖动框选，或使用下方数字框选。每张图最多 10 个区域。</Typography.Paragraph>
        <Space wrap>{current.tasks.map((item, i) => <Button key={item.key} disabled={busy} type={taskKey === item.key ? "primary" : "default"} onClick={() => setTaskKey(item.key)}>{i === 0 ? "整张图片" : `区域 ${i}`}{item.confirmed !== undefined ? " · 已确认" : item.error ? " · 失败" : item.result ? " · 已搜索" : ""}</Button>)}</Space>
        {task?.region && <div className="visual-current-crop" aria-label="当前裁剪区域预览" style={{ aspectRatio: `${task.region[2] * current.aspect} / ${task.region[3]}` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={current.url} alt="当前框选商品预览" style={{ width: `${100 / task.region[2]}%`, left: `${-task.region[0] / task.region[2] * 100}%`, top: `${-task.region[1] / task.region[3] * 100}%` }} />
        </div>}
        <Flex wrap gap={8} className="visual-search-actions">
          <Button type="primary" icon={<SearchOutlined />} loading={busy} disabled={busy || !status?.schema_ready || !status.api_key_configured || running} onClick={() => task && execute([task])}>{task?.result ? "重新搜索当前区域" : task?.region ? "搜索当前区域" : "搜索整张图片"}</Button>
          {regions.length > 1 && <Button disabled={busy || !status?.schema_ready || !status.api_key_configured || running} onClick={() => execute(current.tasks.filter((t) => t.region))}>依次搜索全部区域</Button>}
          {task?.region && <Button disabled={busy} onClick={() => { setPhotos((old) => old.map((p) => p.id === current.id ? { ...p, tasks: p.tasks.filter((t) => t.key !== taskKey) } : p)); setTaskKey("whole"); }}>删除当前区域</Button>}
        </Flex>
      </Card>
      <Card title={task?.region ? `区域 ${selectedRegion + 1} 的候选商品` : "整张图片的候选商品"} loading={busy && !task?.result && !task?.error}>
        {task?.error && <Alert type="error" showIcon title={task.error} />}
        {!task?.result && !task?.error && <Empty description="执行搜索后显示最多 5 款候选商品" />}
        {task?.result && <>
          <Typography.Paragraph type="secondary">{task.result.message} · 耗时 {(task.result.elapsed_ms / 1000).toFixed(2)} 秒</Typography.Paragraph>
          <Image width={100} src={`${getApiBaseUrl()}/api/visual-search/queries/${task.result.query_id}/image`} alt="本次实际用于检索的商品区域" />
          {task.result.candidates.length === 0 && <Empty description="没有匹配候选，可调整区域后重新搜索" />}
          {task.result.candidates.map(({ product, similarity }, i) => <div className="visual-candidate" key={product.id}>
            <ProductImage imagePath={product.image_path} thumbnailPath={product.thumbnail_path} alt={product.product_code ?? `商品 ${product.id}`} width={100} height={100} />
            <div className="visual-candidate-info">
              <Flex justify="space-between" gap={8}><Typography.Text strong>{i + 1}. {product.product_code ?? `商品 ${product.id}`}</Typography.Text><Tag>相似度 {similarity.toFixed(3)}</Tag></Flex>
              <Typography.Paragraph type="secondary">{product.category_name || "未分类"} · {product.warehouse_name || "未关联仓库"}</Typography.Paragraph>
              <Typography.Paragraph>包装：{product.packagings.map((p) => p.packing_qty === null ? "未填装箱数" : `${p.packing_qty} ${product.unit === "set" ? "套" : "件"}/箱`).join("、") || "未填写"}{product.size && ` · 尺寸 ${product.size}`}</Typography.Paragraph>
              <Space wrap><Button type={task.confirmed === product.id ? "primary" : "default"} disabled={busy} onClick={() => feedback(product.id)}>{task.confirmed === product.id ? "已选定" : "选定此商品"}</Button><Link href={`/products/detail?id=${product.id}&returnTo=%2Fvisual-search`} target="_blank">查看商品详情</Link></Space>
            </div>
          </div>)}
          <Button disabled={busy} onClick={() => feedback(null)}>{task.confirmed === "none" ? "已记录：没有正确商品" : "这些候选中没有正确商品"}</Button>
        </>}
      </Card>
    </div>}
    <Modal title="图库索引" open={indexOpen} onCancel={() => { if (!indexBusy) { setIndexOpen(false); setPlan(undefined); } }} footer={null}>
      {status && <><Typography.Paragraph>已索引 {status.indexed} · 待索引 {status.unindexed} · 失败 {status.failed} · 跳过 {status.skipped}</Typography.Paragraph><Typography.Paragraph type="secondary">{status.model} · {status.dimension} 维 · {status.version}</Typography.Paragraph></>}
      <Alert type="info" title="先预检，再确认本批付费索引" description="未变化图片会跳过。失败图片可通过下一批增量任务重试。每批最多 100 款。" />
      <Space className="visual-search-actions"><span>本批最多</span><InputNumber min={1} max={100} precision={0} value={limit} disabled={indexBusy} onChange={(value) => { setLimit(value ?? 10); setPlan(undefined); }} /><span>款</span><Button loading={indexBusy} onClick={previewIndex}>预检（不调用 API）</Button></Space>
      {plan && <><Typography.Paragraph>本批预计处理 {plan.would_process} 款，待处理共 {plan.pending_total} 款；跳过未变化 {plan.unchanged} 款，清理失效索引 {plan.would_clean} 条。</Typography.Paragraph><Button type="primary" disabled={indexBusy || !status?.schema_ready || !status.api_key_configured || running} onClick={() => setPaidConfirm(true)}>确认本批索引</Button></>}
      {status?.jobs.map((job) => <Typography.Paragraph key={job.id} type="secondary">任务 {job.id.slice(0, 8)} · {({ running: "进行中", completed: "已完成", failed: "失败", interrupted: "已中断" } as Record<string, string>)[job.status]} · 处理 {job.processed} / 成功 {job.succeeded} / 失败 {job.failed}</Typography.Paragraph>)}
    </Modal>
    <Modal title="确认付费调用" open={paidConfirm} onCancel={() => !indexBusy && setPaidConfirm(false)} onOk={startIndex} confirmLoading={indexBusy} okText="开始本批索引" cancelText="取消">
      <Typography.Paragraph>最多处理 {limit} 款商品。模型异常时可能重试，每次实际调用均会记录。请确认百炼额度及当前价格。</Typography.Paragraph>
    </Modal>
  </div>;
}
