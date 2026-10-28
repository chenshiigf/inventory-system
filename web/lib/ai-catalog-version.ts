import { getVisualCatalogVersion, type VisualCatalogVersion } from "@/lib/api/visual-search";

export class CatalogVersionError extends Error {
  constructor(readonly reason: "changed" | "unavailable") {
    super(reason === "changed" ? "商品目录已更新，旧结果已清除，请重新搜索。" : "目录版本暂时无法核验，已停用旧结果；请稍后重试或到商品管理页核对。");
  }
}

export function assertCatalogVersion(expected: VisualCatalogVersion, actual: VisualCatalogVersion): void {
  if (!actual || typeof actual !== "object" || actual.source !== "production-catalog" || actual.source_id !== "aliyun-inventory-production"
    || typeof actual.index_namespace !== "string" || !/^production-[a-f0-9]{64}$/.test(actual.index_namespace)
    || typeof actual.catalog_fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(actual.catalog_fingerprint)
    || (actual.catalog_revision !== undefined && !/^[a-f0-9]{64}$/.test(actual.catalog_revision))) {
    throw new CatalogVersionError("unavailable");
  }
  if (["source", "source_id", "index_namespace", "catalog_fingerprint", "catalog_revision"].some(key => expected[key as keyof VisualCatalogVersion] !== actual[key as keyof VisualCatalogVersion])) {
    throw new CatalogVersionError("changed");
  }
}

export async function verifyCatalogVersion(expected: VisualCatalogVersion, load = getVisualCatalogVersion): Promise<void> {
  let actual: VisualCatalogVersion;
  try { actual = await load(); }
  catch { throw new CatalogVersionError("unavailable"); }
  assertCatalogVersion(expected, actual);
}
