import os
import re
from pathlib import Path
import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DOCX = PROJECT_ROOT / "outputs" / "CrimeWatch_VIT_Project_Report.docx"
MD_FILE = PROJECT_ROOT / "outputs" / "CrimeWatch_VIT_Project_Report.md"

def set_cell_background(cell, fill_hex):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    tcPr.append(shd)

def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)

def add_callout_box(doc, text, title="[SCREENSHOT PLACEHOLDER]"):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Cm(14.5)
    
    cell = table.cell(0, 0)
    set_cell_background(cell, "F1F5F9")
    set_cell_margins(cell, top=160, bottom=160, left=240, right=240)
    
    # Border
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="6" w:space="0" w:color="0284C7"/>'
        f'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="0284C7"/>'
        f'<w:left w:val="single" w:sz="24" w:space="0" w:color="0284C7"/>'
        f'<w:right w:val="single" w:sz="6" w:space="0" w:color="0284C7"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(borders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.15
    run_title = p.add_run(f"{title}\n")
    run_title.bold = True
    run_title.font.name = "Times New Roman"
    run_title.font.size = Pt(11)
    run_title.font.color.rgb = RGBColor(2, 132, 199)
    
    run_body = p.add_run(text)
    run_body.font.name = "Times New Roman"
    run_body.font.size = Pt(10.5)
    run_body.font.color.rgb = RGBColor(51, 65, 85)
    
    doc.add_paragraph().paragraph_format.space_after = Pt(6)

def build_report():
    doc = Document()
    
    # ── Page setup (A4 & VIT Margins) ──────────────────────────────────────────
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(3.5)
    section.right_margin = Cm(3.0)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    
    # Set default style to Times New Roman
    style_normal = doc.styles['Normal']
    style_normal.font.name = 'Times New Roman'
    style_normal.font.size = Pt(12)
    style_normal.font.color.rgb = RGBColor(0, 0, 0)
    
    # ── Title Page ─────────────────────────────────────────────────────────────
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(36)
    p.paragraph_format.space_after = Pt(12)
    p.paragraph_format.line_spacing = 1.3
    
    run = p.add_run("CRIMEWATCH:\nAUTONOMOUS REAL-TIME CCTV CRIME DETECTION AND EMERGENCY DISPATCH SYSTEM USING SPATIOTEMPORAL VISION TRANSFORMERS\n\n")
    run.bold = True
    run.font.size = Pt(16)
    run.font.color.rgb = RGBColor(15, 23, 42)
    
    run_sub = p.add_run("A PROJECT REPORT\nSubmitted by\n\n")
    run_sub.bold = True
    run_sub.font.size = Pt(13)
    
    run_auth = p.add_run("SANJAY CHAUHAN (Reg No: 21BCE0000)\n\n")
    run_auth.bold = True
    run_auth.font.size = Pt(13)
    run_auth.font.color.rgb = RGBColor(30, 41, 59)
    
    run_course = p.add_run("Course Code & Name: CSE4001 - Project Exhibition\nSlot: EPJ - Review III\n\nProject Guide:\nProf. Guide Name\nDesignation, SCOPE\n\n")
    run_course.font.size = Pt(12)
    
    run_deg = p.add_run("BACHELOR OF TECHNOLOGY IN COMPUTER SCIENCE AND ENGINEERING\n\nSCHOOL OF COMPUTER SCIENCE AND ENGINEERING (SCOPE)\nVELLORE INSTITUTE OF TECHNOLOGY (VIT)\nVELLORE - 632014, TAMIL NADU, INDIA\n\nFALL SEMESTER 2026-27")
    run_deg.bold = True
    run_deg.font.size = Pt(12)
    
    doc.add_page_break()
    
    # Read markdown content
    with open(MD_FILE, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Helper to add standard chapter header
    def add_chapter_title(title_text):
        doc.add_page_break()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(18)
        run = p.add_run(title_text.upper())
        run.bold = True
        run.font.size = Pt(15)
        run.font.color.rgb = RGBColor(15, 23, 42)
        return p

    def add_section_title(sec_text):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
        run = p.add_run(sec_text)
        run.bold = True
        run.font.size = Pt(13)
        run.font.color.rgb = RGBColor(30, 41, 59)
        return p

    def add_subsection_title(subsec_text):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        run = p.add_run(subsec_text)
        run.bold = True
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(51, 65, 85)
        return p

    def add_body_p(text, space_after=6, bold=False, italic=False):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.line_spacing = 1.5
        p.paragraph_format.space_after = Pt(space_after)
        run = p.add_run(text)
        run.font.size = Pt(12)
        run.font.name = "Times New Roman"
        run.bold = bold
        run.italic = italic
        return p

    def add_code_block(code_text):
        table = doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        table.columns[0].width = Cm(14.5)
        cell = table.cell(0, 0)
        set_cell_background(cell, "0F172A")
        set_cell_margins(cell, top=140, bottom=140, left=200, right=200)
        
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.15
        run = p.add_run(code_text.strip())
        run.font.name = "Consolas"
        run.font.size = Pt(9.5)
        run.font.color.rgb = RGBColor(241, 245, 249)
        doc.add_paragraph().paragraph_format.space_after = Pt(6)

    def add_parsed_table(headers, rows_data):
        table = doc.add_table(rows=len(rows_data) + 1, cols=len(headers))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True
        
        # Header
        hdr_row = table.rows[0]
        for idx, h_text in enumerate(headers):
            cell = hdr_row.cells[idx]
            set_cell_background(cell, "1E293B")
            set_cell_margins(cell, top=120, bottom=120, left=140, right=140)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(h_text.strip())
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(255, 255, 255)
            
        # Data
        for r_idx, row_items in enumerate(rows_data):
            row = table.rows[r_idx + 1]
            bg_color = "F8FAFC" if (r_idx % 2 == 0) else "FFFFFF"
            for c_idx, val in enumerate(row_items):
                if c_idx < len(row.cells):
                    cell = row.cells[c_idx]
                    set_cell_background(cell, bg_color)
                    set_cell_margins(cell, top=80, bottom=80, left=120, right=120)
                    p = cell.paragraphs[0]
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT if c_idx == 0 or len(val) > 15 else WD_ALIGN_PARAGRAPH.CENTER
                    run = p.add_run(val.strip())
                    run.font.size = Pt(9.5)
                    run.font.color.rgb = RGBColor(30, 41, 59)
        doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # ── Parse markdown sections ────────────────────────────────────────────────
    # We parse the markdown and construct sections
    lines = md_text.splitlines()
    in_code = False
    code_buf = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        if line.startswith("```"):
            if in_code:
                add_code_block("\n".join(code_buf))
                code_buf = []
                in_code = False
            else:
                in_code = True
                code_buf = []
            i += 1
            continue
            
        if in_code:
            code_buf.append(line)
            i += 1
            continue
            
        # Check image screenshot placeholders
        if "[INSERT SCREENSHOT:" in line:
            desc = line.replace("[INSERT SCREENSHOT:", "").replace("]", "").strip()
            
            # If it's confusion matrix, insert real image!
            if "confusion_matrix" in desc.lower():
                cm_img = PROJECT_ROOT / "outputs" / "videomae_confusion_matrix.png"
                if cm_img.exists():
                    p_img = doc.add_paragraph()
                    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p_img.paragraph_format.space_before = Pt(8)
                    p_img.paragraph_format.space_after = Pt(4)
                    run_img = p_img.add_run()
                    run_img.add_picture(str(cm_img), width=Cm(13.8))
                    
                    p_cap = doc.add_paragraph()
                    p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p_cap.paragraph_format.space_after = Pt(12)
                    run_cap = p_cap.add_run("Figure 5.1: High-Resolution Test Confusion Matrix (71.4% Overall Accuracy across 70 Held-out Surveillance Videos)")
                    run_cap.font.size = Pt(10)
                    run_cap.bold = True
                    run_cap.font.color.rgb = RGBColor(71, 85, 105)
                else:
                    add_callout_box(doc, f"Insert {desc} here (Resolution: 300 DPI, centered)", title="[FIGURE ARTIFACT]")
            elif "training_curves" in desc.lower():
                tc_img = PROJECT_ROOT / "outputs" / "videomae_training_curves.png"
                if tc_img.exists():
                    p_img = doc.add_paragraph()
                    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p_img.paragraph_format.space_before = Pt(8)
                    p_img.paragraph_format.space_after = Pt(4)
                    run_img = p_img.add_run()
                    run_img.add_picture(str(tc_img), width=Cm(14.2))
                    
                    p_cap = doc.add_paragraph()
                    p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p_cap.paragraph_format.space_after = Pt(12)
                    run_cap = p_cap.add_run("Figure 5.2: 10-Epoch Training and Validation Convergence Dynamics (Loss & Accuracy)")
                    run_cap.font.size = Pt(10)
                    run_cap.bold = True
                    run_cap.font.color.rgb = RGBColor(71, 85, 105)
                else:
                    add_callout_box(doc, f"Insert {desc} here (Resolution: 300 DPI, centered)", title="[FIGURE ARTIFACT]")
            else:
                add_callout_box(doc, f"Capture and paste: {desc}\n(Recommended size: 14 cm width, centered with 1 pt border)", title="[UI SCREENSHOT PLACEHOLDER]")
            i += 1
            continue

        # Check Chapter Headings
        if line.strip().startswith("CHAPTER "):
            chap_num = line.strip()
            chap_title = lines[i+1].strip() if i+1 < len(lines) else ""
            add_chapter_title(f"{chap_num}\n{chap_title}")
            i += 2
            continue

        if line.strip() in ["ABSTRACT", "TABLE OF CONTENTS", "LIST OF TABLES", "LIST OF FIGURES", "LIST OF ABBREVIATIONS & SYMBOLS", "REFERENCES", "APPENDICES"]:
            add_chapter_title(line.strip())
            i += 1
            continue

        # Check Sections e.g. "1.1 BACKGROUND & MOTIVATION"
        if re.match(r"^\d+\.\d+\s+[A-Z]", line.strip()):
            add_section_title(line.strip())
            i += 1
            continue

        # Check Subsections e.g. "3.2.1 Video Patch Tokenization"
        if re.match(r"^\d+\.\d+\.\d+\s+", line.strip()):
            add_subsection_title(line.strip())
            i += 1
            continue

        # Check Tables with ASCII borders
        if line.strip().startswith("┌") or line.strip().startswith("+--"):
            # Accumulate table lines
            table_lines = [line]
            i += 1
            while i < len(lines) and (lines[i].strip().startswith("│") or lines[i].strip().startswith("├") or lines[i].strip().startswith("└") or lines[i].strip().startswith("|")):
                table_lines.append(lines[i])
                if lines[i].strip().startswith("└"):
                    i += 1
                    break
                i += 1
                
            # Parse table data
            content_rows = [tl for tl in table_lines if tl.strip().startswith("│") or (tl.strip().startswith("|") and not tl.strip().startswith("|--"))]
            if content_rows:
                raw_headers = [c.strip() for c in content_rows[0].split("│")[1:-1]] if "│" in content_rows[0] else [c.strip() for c in content_rows[0].split("|")[1:-1]]
                data_rows = []
                for cr in content_rows[1:]:
                    cells = [c.strip() for c in cr.split("│")[1:-1]] if "│" in cr else [c.strip() for c in cr.split("|")[1:-1]]
                    if cells:
                        data_rows.append(cells)
                if raw_headers and data_rows:
                    add_parsed_table(raw_headers, data_rows)
            continue

        # Check Table Caption e.g. "Table 3.1: CrimeWatch Target Classes..."
        if line.strip().startswith("Table ") and ":" in line:
            p_tab = doc.add_paragraph()
            p_tab.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_tab.paragraph_format.space_before = Pt(10)
            p_tab.paragraph_format.space_after = Pt(4)
            r_tab = p_tab.add_run(line.strip())
            r_tab.bold = True
            r_tab.font.size = Pt(10.5)
            r_tab.font.color.rgb = RGBColor(30, 41, 59)
            i += 1
            continue

        # Standard Paragraphs
        stripped = line.strip()
        if stripped and not stripped.startswith("===") and not stripped.startswith("---") and not stripped.startswith("┌") and not stripped.startswith("└"):
            add_body_p(stripped)
            
        i += 1

    doc.save(str(OUTPUT_DOCX))
    print(f"[SUCCESS] Created DOCX report: {OUTPUT_DOCX}")

if __name__ == "__main__":
    build_report()

