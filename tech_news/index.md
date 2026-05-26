---
layout: "default"
title: "Latest Tech News"
permalink: "/tech_news/"
mode: "tech_news"
---

# Latest Tech News

{% assign mode_posts = site.posts | where: "mode", "tech_news" %}
{% assign latest_mode = mode_posts.first %}
{% if latest_mode %}
<p class="aurora-back"><a href="{{ latest_mode.url | relative_url }}">Archive permalink</a></p>

{{ latest_mode.content }}
{% else %}
No dedicated Tech News digest has been published yet.

{% assign latest = site.posts.first %}
{% if latest %}
See the [latest published digest]({{ latest.url | relative_url }}) for current Aurora output.
{% endif %}
{% endif %}
