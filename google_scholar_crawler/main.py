# from scholarly import scholarly
from serpapi import GoogleSearch
import json
from datetime import datetime
import os
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def get_scholar_citations(author_id: str) -> int:
    """
    使用 SerpApi 获取指定 Google Scholar 用户的总引用次数。

    参数:
        author_id: Google Scholar 用户的 Profile ID（URL 中 `user=` 后的值）。

    返回:
        总引用次数（int）。若未获取到，则返回 -1。
    """
    # 从环境变量读取 API Key
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        raise RuntimeError("请先在环境变量中设置 SERPAPI_API_KEY")

    # SerpApi 请求参数
    params = {
        "engine": "google_scholar_author",
        "author_id": author_id,
        "api_key": api_key,
    }

    # 发起请求
    search = GoogleSearch(params)
    result = search.get_dict()

    author_info = {
        "name": result["author"]["name"],
        "publications": {a["citation_id"]: a for a in result["articles"]},
        "citedby": result["cited_by"]["table"][0]["citations"]["all"],
        "updated": str(datetime.now()),
    }

    return author_info


try:

    google_scholar_id = os.environ["GOOGLE_SCHOLAR_ID"]
    logging.info(f"开始获取 Google Scholar ID: {google_scholar_id} 的数据")

    # 获取作者信息
    author = get_scholar_citations(google_scholar_id)
    logging.info(f"成功获取作者 {author['name']} 的数据")

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
