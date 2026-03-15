"""Monkey-patch for jvav DmmUtil to fix DMM layout change."""
import re
from typing import Tuple

def patched_get_nice_avs_by_star_name(self, star_name: str) -> Tuple[int, any]:
    url = self.base_url_search_star + star_name + "%20単体"
    code, resp = self.send_req(
        url=url,
        headers={"user-agent": self.ua_desktop()},
        cookies={"age_check_done": "1"},
    )
    if code != 200:
        return code, resp
    try:
        soup = self.get_soup(resp)
        grids = soup.find_all(class_="grid")
        av_list = None
        for g in grids:
            divs = g.find_all("div", recursive=False)
            if len(divs) >= 5:
                av_list = g
                break
        if not av_list:
            return 404, None
        av_tags = av_list.find_all("div", recursive=False)
        avs = []
        for av in av_tags:
            try:
                links = av.find_all("a", href=True)
                cid = None
                for a_tag in links:
                    href = a_tag["href"]
                    # Handle both: content=xxx and id=xxx
                    m = re.search(r"(?:content|id)=([^&/]+)", href)
                    if m:
                        cid = m.group(1)
                        break
                if not cid:
                    continue
                av_id = self.get_id_by_cid(cid)
                # Find score - look for pattern like "4.8（884件）"
                score = None
                spans = av.find_all("span")
                for span in spans:
                    text = span.get_text(strip=True)
                    m = re.match(r"([\d.]+)[（(]", text)
                    if m:
                        score = float(m.group(1))
                        break
                avs.append({"rate": score, "id": av_id})
            except Exception:
                pass
        if not avs:
            return 404, None
        avs = [av for av in avs if av["rate"] is not None and av["rate"] >= 4.0]
        if not avs:
            return 404, None
        return 200, avs
    except Exception:
        return 404, None
