---
layout: default
title: Aurora
---

# Aurora

Aurora publishes a personal intelligence digest across research papers, GitHub repositories, and timely technology news.

<nav class="aurora-mode-nav">
  <a href="{{ '/unified_digest/' | relative_url }}">Unified Digest</a>
  <a href="{{ '/scholar/' | relative_url }}">Scholar</a>
  <a href="{{ '/repo_learning/' | relative_url }}">Repo Learning</a>
  <a href="{{ '/tech_news/' | relative_url }}">Tech News</a>
  <a href="{{ '/feed.xml' | relative_url }}">RSS</a>
</nav>

## Latest Digest Pages

- [Unified Digest]({{ '/unified_digest/' | relative_url }})
- [Scholar]({{ '/scholar/' | relative_url }})
- [Repo Learning]({{ '/repo_learning/' | relative_url }})
- [Tech News]({{ '/tech_news/' | relative_url }})

## Archive

{% if site.posts.size > 0 %}
{% for post in site.posts limit: 30 %}
- [{{ post.title }}]({{ post.url | relative_url }}) <span class="aurora-pill">{{ post.mode }}</span> {{ post.date | date: "%Y-%m-%d" }}
{% endfor %}
{% else %}
No digest posts have been published yet.
{% endif %}
