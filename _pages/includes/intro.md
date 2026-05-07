
{% if site.google_scholar_stats_use_cdn %}
{% assign gsDataBaseUrl = "https://cdn.jsdelivr.net/gh/" | append: site.repository | append: "@" %}
{% else %}
{% assign gsDataBaseUrl = "https://raw.githubusercontent.com/" | append: site.repository | append: "/" %}
{% endif %}
{% assign url = gsDataBaseUrl | append: "google-scholar-stats/gs_data_shieldsio.json" %}

<span class='anchor' id='about-me'></span>


Zhiwei Li (李志伟), born in Zhengzhou, Henan, China, in 1997, is a PhD candidate at the [Australian Artificial Intelligence Institute (AAII)](https://www.uts.edu.au/research/australian-artificial-intelligence-institute), University of Technology Sydney, advised by [Prof. Guodong Long](https://guodonglong.github.io/). His research centers on **privacy-preserving personalized recommender systems**, especially under federated, multimodal, and foundation-model settings. His work has appeared at top venues including ICLR, AAAI, and IJCAI <a href='https://scholar.google.com/citations?user=b3glA2AAAAAJ'><img src="https://img.shields.io/endpoint?url={{ url | url_encode }}&logo=Google%20Scholar&labelColor=f6f6f6&color=9cf&style=flat&label=citations"></a>.

He received his M.S. in Computer Science from [ShanghaiTech University](https://www.shanghaitech.edu.cn/) in 2023, advised by [Prof. Lu Sun](https://lusun912.github.io/), with research on multi-view multi-label learning.

He completed his B.E. at [Zhengzhou University](http://www.zzu.edu.cn/) in 2020, where he was a member of the [ZZU-DROID](https://baike.baidu.com/item/ZZU-DROID/23540312?fromModule=search-result_lemma-recommend) team at the Bipedal Robot Laboratory (2017.06–2020.06), competing in RoboCup events.

His full CV is available as a [PDF](images/cv.pdf).

Research interests:
- *2023 ~ NOW*: Privacy-preserving personalized recommender systems, especially under federated, multimodal, and foundation-model settings.
- *2020 ~ 2023*: Multi-view multi-label learning.

> <span style="color:red">*Remark*</span>: I am open to collaborations with researchers and practitioners in academia and industry on topics including privacy-preserving recommendation, federated learning, and foundation-model integration. Feel free to reach out.
