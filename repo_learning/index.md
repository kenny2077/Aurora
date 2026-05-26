---
layout: "default"
title: "Latest Repo Learning"
permalink: "/repo_learning/"
mode: "repo_learning"
---

# Latest Repo Learning

{% assign mode_posts = site.posts | where: "mode", "repo_learning" %}
{% assign latest_mode = mode_posts.first %}
{% if latest_mode %}
<p class="aurora-back"><a href="{{ latest_mode.url | relative_url }}">Archive permalink</a></p>

{{ latest_mode.content }}
{% else %}
No dedicated Repo Learning digest has been published yet.

{% assign latest = site.posts.first %}
{% if latest %}
See the [latest published digest]({{ latest.url | relative_url }}) for current Aurora output.
{% endif %}
{% endif %}
