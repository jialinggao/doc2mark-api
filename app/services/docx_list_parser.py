from docx import Document
from docx.oxml.ns import qn
from typing import Dict, Tuple, Optional, List, Any
import re
from loguru import logger


class DocxListParser:
    def __init__(self):
        self.num_id_to_abstract_id: Dict[int, int] = {}
        self.abstract_num_info: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self.list_counters: Dict[int, int] = {}
    
    def parse_numbering(self, doc: Document) -> None:
        """解析DOCX文档中的编号定义"""
        self.num_id_to_abstract_id.clear()
        self.abstract_num_info.clear()
        self.list_counters.clear()
        
        numbering_part = doc.part.numbering_part
        if not numbering_part:
            return
        
        numbering_element = numbering_part._element
        
        for num in numbering_element.findall(qn("w:num")):
            num_id_attr = num.get(qn("w:numId"))
            if num_id_attr is None:
                continue
            
            abstract_num_id_elem = num.find(qn("w:abstractNumId"))
            if abstract_num_id_elem is None:
                continue
            
            abstract_num_id_attr = abstract_num_id_elem.get(qn("w:val"))
            if abstract_num_id_attr is None:
                continue
            
            try:
                num_id = int(num_id_attr)
                abstract_num_id = int(abstract_num_id_attr)
                self.num_id_to_abstract_id[num_id] = abstract_num_id
                self.list_counters[num_id] = 0
            except (ValueError, TypeError):
                continue
        
        for abstract_num in numbering_element.findall(qn("w:abstractNum")):
            abstract_num_id_attr = abstract_num.get(qn("w:abstractNumId"))
            if abstract_num_id_attr is None:
                continue
            
            try:
                abstract_num_id = int(abstract_num_id_attr)
            except (ValueError, TypeError):
                continue
            
            for lvl in abstract_num.findall(qn("w:lvl")):
                ilvl_attr = lvl.get(qn("w:ilvl"))
                if ilvl_attr is None:
                    continue
                
                try:
                    ilvl = int(ilvl_attr)
                except (ValueError, TypeError):
                    continue
                
                info = {}
                
                lvl_text_elem = lvl.find(qn("w:lvlText"))
                if lvl_text_elem is not None:
                    info['lvlText'] = lvl_text_elem.get(qn("w:val"), "")
                
                num_fmt_elem = lvl.find(qn("w:numFmt"))
                if num_fmt_elem is not None:
                    info['numFmt'] = num_fmt_elem.get(qn("w:val"), "decimal")
                
                start_elem = lvl.find(qn("w:start"))
                if start_elem is not None:
                    start_val = start_elem.get(qn("w:val"), "1")
                    try:
                        info['start'] = int(start_val)
                    except (ValueError, TypeError):
                        info['start'] = 1
                
                if info:
                    self.abstract_num_info[(abstract_num_id, ilvl)] = info
    
    def _convert_number(self, number: int, num_fmt: str) -> str:
        """根据编号样式转换数字格式"""
        num_fmt = num_fmt.lower()
        
        if num_fmt == "decimal":
            return str(number)
        elif num_fmt == "chinesenum1" or num_fmt == "chinesecounting":
            return self._to_chinese_number(number)
        elif num_fmt == "chinesenum2":
            return self._to_chinese_number_traditional(number)
        elif num_fmt == "roman":
            return self._to_roman(number).upper()
        elif num_fmt == "lowerroman":
            return self._to_roman(number).lower()
        elif num_fmt == "ordinal":
            return str(number) + "º"
        elif num_fmt == "cardtext":
            return self._to_chinese_number(number)
        elif num_fmt == "letter":
            return chr(ord('A') + number - 1)
        elif num_fmt == "lowerletter":
            return chr(ord('a') + number - 1)
        elif num_fmt == "ordinaltext":
            return self._to_chinese_number(number) + "号"
        else:
            return str(number)
    
    def _to_chinese_number(self, number: int) -> str:
        """转换为中文数字（一、二、三...）"""
        if number <= 0:
            return str(number)
        
        chinese_nums = "零一二三四五六七八九十"
        
        if number <= 10:
            return chinese_nums[number]
        elif number < 100:
            tens = number // 10
            ones = number % 10
            if ones == 0:
                return chinese_nums[tens] + "十"
            else:
                if tens == 1:
                    return "十" + chinese_nums[ones]
                return chinese_nums[tens] + "十" + chinese_nums[ones]
        elif number < 1000:
            hundreds = number // 100
            remainder = number % 100
            result = chinese_nums[hundreds] + "百"
            if remainder > 0:
                if remainder < 10:
                    result += "零" + chinese_nums[remainder]
                else:
                    result += self._to_chinese_number(remainder)
            return result
        elif number < 10000:
            thousands = number // 1000
            remainder = number % 1000
            result = chinese_nums[thousands] + "千"
            if remainder > 0:
                if remainder < 100:
                    result += "零"
                result += self._to_chinese_number(remainder)
            return result
        else:
            return str(number)
    
    def _to_chinese_number_traditional(self, number: int) -> str:
        """转换为大写中文数字（壹、贰、叁...）"""
        chinese_nums = "零壹贰叁肆伍陆柒捌玖拾"
        if number <= 10:
            return chinese_nums[number]
        elif number < 20:
            return "拾" + chinese_nums[number - 10]
        else:
            return str(number)
    
    def _to_roman(self, number: int) -> str:
        """转换为罗马数字"""
        roman_numerals = [
            (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
            (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),
            (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')
        ]
        result = []
        for value, numeral in roman_numerals:
            while number >= value:
                result.append(numeral)
                number -= value
            if number == 0:
                break
        return ''.join(result)
    
    def get_paragraph_list_number(self, paragraph) -> Optional[str]:
        """获取段落的实际列表编号文本"""
        p_pr = paragraph._element.find(qn("w:pPr"))
        if p_pr is None:
            return None
        
        num_pr = p_pr.find(qn("w:numPr"))
        if num_pr is None:
            return None
        
        num_id_elem = num_pr.find(qn("w:numId"))
        if num_id_elem is None:
            return None
        
        num_id_val = num_id_elem.get(qn("w:val"))
        if num_id_val is None:
            return None
        
        try:
            num_id = int(num_id_val)
        except (ValueError, TypeError):
            return None
        
        ilvl_elem = num_pr.find(qn("w:ilvl"))
        if ilvl_elem is None:
            ilvl = 0
        else:
            ilvl_val = ilvl_elem.get(qn("w:ilvl"))
            if ilvl_val is None:
                ilvl = 0
            else:
                try:
                    ilvl = int(ilvl_val)
                except (ValueError, TypeError):
                    return None
        
        if num_id not in self.num_id_to_abstract_id:
            return None
        
        abstract_num_id = self.num_id_to_abstract_id[num_id]
        key = (abstract_num_id, ilvl)
        
        if key not in self.abstract_num_info:
            return None
        
        info = self.abstract_num_info[key]
        lvl_text = info.get('lvlText', "")
        
        if not lvl_text:
            return None
        
        start = info.get('start', 1)
        num_fmt = info.get('numFmt', 'decimal')
        
        if num_id not in self.list_counters:
            self.list_counters[num_id] = 0
        self.list_counters[num_id] += 1
        current_num = start + self.list_counters[num_id] - 1
        
        converted_num = self._convert_number(current_num, num_fmt)
        
        result = lvl_text.replace("%1", converted_num)
        
        for i in range(2, 10):
            placeholder = f"%{i}"
            if placeholder in result:
                result = result.replace(placeholder, self._convert_number(i, num_fmt))
        
        return result
    
    def extract_all_list_items(self, doc: Document) -> List[Tuple[str, str]]:
        """提取所有列表项及其编号"""
        self.parse_numbering(doc)
        
        result = []
        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:
                list_num = self.get_paragraph_list_number(paragraph)
                if list_num:
                    result.append((list_num, text))
        
        return result
    
    def extract_list_items_with_style(self, doc: Document) -> List[Tuple[str, str]]:
        """使用样式名称提取列表项（备用方法）"""
        result = []
        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:
                style_name = paragraph.style.name if paragraph.style else ""
                if "List" in style_name or "列表" in style_name or "ListParagraph" in style_name:
                    p_pr = paragraph._element.find(qn("w:pPr"))
                    if p_pr is not None:
                        num_pr = p_pr.find(qn("w:numPr"))
                        if num_pr is not None:
                            num_id_elem = num_pr.find(qn("w:numId"))
                            if num_id_elem is not None:
                                num_id = int(num_id_elem.get(qn("w:val")))
                                if num_id not in self.list_counters:
                                    self.list_counters[num_id] = 0
                                self.list_counters[num_id] += 1
                                
                                pattern = r'^([第第]?[\u4e00-\u9fff\d]+[条章节款项]?)\s*'
                                match = re.match(pattern, text)
                                if match:
                                    result.append((match.group(1), text))
                                else:
                                    result.append((str(self.list_counters[num_id]), text))
        return result


docx_list_parser = DocxListParser()