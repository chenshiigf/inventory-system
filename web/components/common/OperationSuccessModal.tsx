"use client";

import { CheckCircleFilled } from "@ant-design/icons";
import { Button, Modal, Typography } from "antd";
import type { ReactNode } from "react";

interface OperationSuccessAction {
  label: ReactNode;
  onClick: () => void;
}

interface OperationSuccessModalProps {
  open: boolean;
  title: ReactNode;
  description: ReactNode;
  primaryAction?: OperationSuccessAction;
  secondaryAction?: OperationSuccessAction;
  onClose: () => void;
}

export default function OperationSuccessModal({
  open,
  title,
  description,
  primaryAction,
  secondaryAction,
  onClose,
}: OperationSuccessModalProps) {
  return (
    <Modal
      className="operation-success-modal"
      open={open}
      width={680}
      centered
      onCancel={onClose}
      title={
        <div className="operation-success-modal-title">
          <CheckCircleFilled aria-hidden="true" />
          <Typography.Title level={4}>{title}</Typography.Title>
        </div>
      }
      footer={
        primaryAction || secondaryAction ? (
          <div className="operation-success-modal-actions">
            {secondaryAction && (
              <Button onClick={secondaryAction.onClick}>{secondaryAction.label}</Button>
            )}
            {primaryAction && (
              <Button type="primary" onClick={primaryAction.onClick}>
                {primaryAction.label}
              </Button>
            )}
          </div>
        ) : null
      }
    >
      <Typography.Paragraph className="operation-success-modal-description" type="secondary">
        {description}
      </Typography.Paragraph>
    </Modal>
  );
}
