from app.database import SessionLocal
from app.product_codes import backfill_product_codes


def main() -> None:
    with SessionLocal.begin() as db:
        result = backfill_product_codes(db)
    print(
        "商品编号回填完成："
        f"新增分类编号 {result['categories_assigned']} 个，"
        f"新增商品编号 {result['products_assigned']} 个。"
    )


if __name__ == "__main__":
    main()
