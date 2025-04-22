from scholarly import scholarly
import json
from datetime import datetime
import os
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

try:
    # 检查环境变量是否存在
    if "GOOGLE_SCHOLAR_ID" not in os.environ:
        raise KeyError("环境变量 'GOOGLE_SCHOLAR_ID' 未设置")

    google_scholar_id = os.environ["GOOGLE_SCHOLAR_ID"]
    logging.info(f"开始获取 Google Scholar ID: {google_scholar_id} 的数据")

    # 获取作者信息
    author: dict = scholarly.search_author_id(google_scholar_id)
    scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])
    name = author["name"]
    author["updated"] = str(datetime.now())
    author["publications"] = {v["author_pub_id"]: v for v in author["publications"]}
    logging.info(f"成功获取作者 {name} 的数据")

    # 输出和保存结果
    print(json.dumps(author, indent=2))
    os.makedirs("results", exist_ok=True)

    with open(f"results/gs_data.json", "w") as outfile:
        json.dump(author, outfile, ensure_ascii=False)

    shieldio_data = {
        "schemaVersion": 1,
        "label": "citations",
        "message": f"{author['citedby']}",
    }

    with open(f"results/gs_data_shieldsio.json", "w") as outfile:
        json.dump(shieldio_data, outfile, ensure_ascii=False)

    logging.info("数据已成功保存到 results 文件夹")

except KeyError as e:
    logging.error(f"错误: {str(e)}")
except Exception as e:
    logging.error(f"发生错误: {str(e)}")
