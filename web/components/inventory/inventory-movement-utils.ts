import type { InventoryMovementApiRecord } from "@/types/inventory";

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatMovementTime(value: string): string {
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatPackaging(movement: InventoryMovementApiRecord): string {
  if (movement.packing_qty_snapshot === null) {
    return "装箱数未填写";
  }
  return `${movement.packing_qty_snapshot}${movement.unit_snapshot ? ` ${movement.unit_snapshot}` : ""}/箱`;
}

export function getMovementLabel(
  type: InventoryMovementApiRecord["movement_type"],
): string {
  if (type === "IN") {
    return "入库";
  }
  if (type === "OUT") {
    return "出库";
  }
  return "库存调整";
}

export function getMovementDelta(movement: InventoryMovementApiRecord): number {
  if (movement.movement_type === "IN") {
    return movement.quantity;
  }
  if (movement.movement_type === "OUT") {
    return -movement.quantity;
  }
  return movement.after_carton_count - movement.before_carton_count;
}

export function formatMovementQuantity(
  movement: InventoryMovementApiRecord,
): string {
  const delta = getMovementDelta(movement);
  return `${delta > 0 ? "+" : ""}${delta}箱`;
}
