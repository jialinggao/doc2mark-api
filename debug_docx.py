#!/usr/bin/env python3
"""调试脚本：检查DOCX列表解析"""

from docx import Document
from app.services.docx_list_parser import docx_list_parser
import sys

def debug_docx(docx_path):
    """调试docx列表解析"""
    doc = Document(docx_path)
    
    print("=== DOCX文档段落分析 ===")
    list_items = docx_list_parser.extract_all_list_items(doc)
    
    print(f"\n找到 {len(list_items)} 个列表项:")
    for i, (num, content) in enumerate(list_items, 1):
        print(f"{i:2d}. [{num}] {content[:60]}...")
    
    if len(list_items) == 0:
        print("\n未找到列表项，检查文档结构...")
        for i, para in enumerate(doc.paragraphs[:30]):
            if para.text.strip():
                print(f"段落 {i}: {para.text[:80]}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python debug_docx.py <docx文件路径>")
        sys.exit(1)
    
    debug_docx(sys.argv[1])