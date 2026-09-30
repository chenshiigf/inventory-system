"use client";

import { useEffect, useRef, useState } from "react";
import { ApiResponseError } from "@/lib/api/client";
import {
  cancelProductImportPreviewTask,
  getProductImportPreviewTask,
  startProductImportPreview,
} from "@/lib/api/product-import";
import type { ProductImportPreviewResponse, ProductImportPreviewTask } from "@/lib/api/product-import";

interface Callbacks {
  onSuccess: (preview: ProductImportPreviewResponse) => void;
  onCancelled: () => void;
  onMissing: () => void;
}

interface Attempt {
  taskId: string | null;
  cancelRequested: boolean;
  controller?: AbortController;
  polling?: ReturnType<typeof setTimeout>;
  timer?: ReturnType<typeof setInterval>;
}

function stop(attempt: Attempt) {
  clearTimeout(attempt.polling);
  clearInterval(attempt.timer);
  attempt.controller?.abort();
}

// Unmount is best effort; server-side session expiry owns eventual cleanup.
function discard(taskId: string) {
  void cancelProductImportPreviewTask(taskId).catch(() => {});
}

export default function useImportPreviewTask(options: Callbacks) {
  const callbacks = useRef(options);
  const active = useRef<Attempt | null>(null);
  const mounted = useRef(false);
  const [uploading, setUploading] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => { callbacks.current = options; }, [options]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      const attempt = active.current;
      active.current = null;
      if (attempt) {
        stop(attempt);
        if (attempt.taskId) discard(attempt.taskId);
      }
    };
  }, []);

  function owns(attempt: Attempt) {
    return mounted.current && active.current === attempt;
  }

  function finish(attempt: Attempt) {
    stop(attempt);
    active.current = null;
    setUploading(false);
    setTaskId(null);
    setCancelling(false);
  }

  function missing(attempt: Attempt) {
    finish(attempt);
    setError(new Error("导入任务已终止或服务器已重启，请重新上传 Excel。"));
    callbacks.current.onMissing();
  }

  function receive(attempt: Attempt, task: ProductImportPreviewTask): boolean {
    if (!owns(attempt)) return true;
    if (task.status === "cancelled") {
      finish(attempt);
      setError(null);
      callbacks.current.onCancelled();
      return true;
    }
    if (task.status === "failed") {
      finish(attempt);
      setError(new Error(task.error ?? "Excel 预览失败，请重试。"));
      return true;
    }
    if (task.status === "succeeded" && !attempt.cancelRequested) {
      finish(attempt);
      if (task.result) {
        setError(null);
        callbacks.current.onSuccess(task.result);
      } else {
        setError(new Error("预览结果缺失，请重新上传 Excel。"));
      }
      return true;
    }
    setError(null);
    return false;
  }

  async function poll(attempt: Attempt) {
    if (!owns(attempt) || !attempt.taskId) return;
    attempt.controller = new AbortController();
    try {
      const task = await getProductImportPreviewTask(attempt.taskId, attempt.controller.signal);
      if (receive(attempt, task)) return;
    } catch (caught) {
      if (!owns(attempt)) return;
      if (caught instanceof ApiResponseError && caught.statusCode === 404) {
        missing(attempt);
        return;
      }
      setError(new Error("暂时无法获取任务状态，将继续重试。你也可以取消本次导入。"));
    }
    if (owns(attempt)) attempt.polling = setTimeout(() => void poll(attempt), 1000);
  }

  async function start(file: File): Promise<boolean> {
    if (active.current || !mounted.current) return false;
    const attempt: Attempt = { taskId: null, cancelRequested: false };
    active.current = attempt;
    setUploading(true);
    setElapsed(0);
    setError(null);
    try {
      const task = await startProductImportPreview(file);
      if (!owns(attempt)) {
        discard(task.task_id);
        return false;
      }
      attempt.taskId = task.task_id;
      setTaskId(task.task_id);
      const startedAt = Date.now();
      attempt.timer = setInterval(() => {
        if (owns(attempt)) setElapsed(Math.floor((Date.now() - startedAt) / 1000));
      }, 1000);
      if (!receive(attempt, task)) void poll(attempt);
      return true;
    } catch (caught) {
      if (!owns(attempt)) return false;
      finish(attempt);
      setError(caught instanceof Error ? caught : new Error("Excel 预览失败，请重试。"));
      return false;
    }
  }

  async function cancel(expectedTaskId: string) {
    const attempt = active.current;
    if (!attempt || attempt.taskId !== expectedTaskId || attempt.cancelRequested) return;
    attempt.cancelRequested = true;
    setCancelling(true);
    try {
      const task = await cancelProductImportPreviewTask(expectedTaskId);
      receive(attempt, task);
    } catch (caught) {
      // An older cancellation must never clear or change a newer attempt.
      if (!owns(attempt)) return;
      if (caught instanceof ApiResponseError && caught.statusCode === 404) {
        missing(attempt);
      } else {
        attempt.cancelRequested = false;
        setCancelling(false);
        setError(caught instanceof Error ? caught : new Error("取消请求失败，请重试。"));
      }
    }
  }

  return {
    uploading, taskId, elapsed, cancelling, error, start, cancel,
    isBusy: () => active.current !== null,
    clearError: () => setError(null),
  };
}
