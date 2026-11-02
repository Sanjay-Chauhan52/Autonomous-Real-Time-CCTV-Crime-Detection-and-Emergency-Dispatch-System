import os
from pathlib import Path
import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DOCX = PROJECT_ROOT / "outputs" / "CrimeWatch_VIT_Project_Report.docx"

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

def add_callout_box(doc, text, title="[SCREENSHOT / FIGURE PLACEHOLDER]"):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Cm(14.5)
    
    cell = table.cell(0, 0)
    set_cell_background(cell, "F1F5F9")
    set_cell_margins(cell, top=140, bottom=140, left=200, right=200)
    
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
    run_body.font.size = Pt(10)
    run_body.font.color.rgb = RGBColor(51, 65, 85)
    
    doc.add_paragraph().paragraph_format.space_after = Pt(6)

def add_code_block(doc, code_text):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Cm(14.5)
    cell = table.cell(0, 0)
    set_cell_background(cell, "0F172A")
    set_cell_margins(cell, top=100, bottom=100, left=160, right=160)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.15
    run = p.add_run(code_text.strip())
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    run.font.color.rgb = RGBColor(241, 245, 249)
    doc.add_paragraph().paragraph_format.space_after = Pt(6)

def add_table_data(doc, headers, rows_data, caption=""):
    if caption:
        p_cap = doc.add_paragraph()
        p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap.paragraph_format.space_before = Pt(10)
        p_cap.paragraph_format.space_after = Pt(4)
        r_cap = p_cap.add_run(caption)
        r_cap.bold = True
        r_cap.font.name = "Times New Roman"
        r_cap.font.size = Pt(10.5)
        r_cap.font.color.rgb = RGBColor(30, 41, 59)

    table = doc.add_table(rows=len(rows_data) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    
    hdr_row = table.rows[0]
    for idx, h_text in enumerate(headers):
        cell = hdr_row.cells[idx]
        set_cell_background(cell, "1E293B")
        set_cell_margins(cell, top=100, bottom=100, left=120, right=120)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(h_text.strip())
        run.bold = True
        run.font.name = "Times New Roman"
        run.font.size = Pt(9.5)
        run.font.color.rgb = RGBColor(255, 255, 255)
        
    for r_idx, row_items in enumerate(rows_data):
        row = table.rows[r_idx + 1]
        bg_color = "F8FAFC" if (r_idx % 2 == 0) else "FFFFFF"
        for c_idx, val in enumerate(row_items):
            if c_idx < len(row.cells):
                cell = row.cells[c_idx]
                set_cell_background(cell, bg_color)
                set_cell_margins(cell, top=80, bottom=80, left=100, right=100)
                p = cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT if c_idx == 0 or len(str(val)) > 20 else WD_ALIGN_PARAGRAPH.CENTER
                run = p.add_run(str(val).strip())
                run.font.name = "Times New Roman"
                run.font.size = Pt(9.5)
                run.font.color.rgb = RGBColor(30, 41, 59)
                
    doc.add_paragraph().paragraph_format.space_after = Pt(8)

def add_img_figure(doc, img_path, caption, width_cm=14.0):
    if Path(img_path).exists():
        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.paragraph_format.space_before = Pt(8)
        p_img.paragraph_format.space_after = Pt(4)
        run_img = p_img.add_run()
        run_img.add_picture(str(img_path), width=Cm(width_cm))
        
        p_cap = doc.add_paragraph()
        p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap.paragraph_format.space_after = Pt(12)
        run_cap = p_cap.add_run(caption)
        run_cap.font.name = "Times New Roman"
        run_cap.font.size = Pt(10)
        run_cap.bold = True
        run_cap.font.color.rgb = RGBColor(71, 85, 105)
    else:
        add_callout_box(doc, f"Image file not found: {img_path}", title="[FIGURE ARTIFACT]")

def build_55_page_report():
    doc = Document()
    
    # ── Page Setup (A4 & VIT Margins) ──────────────────────────────────────────
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(3.5)
    section.right_margin = Cm(3.0)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    
    style_normal = doc.styles['Normal']
    style_normal.font.name = 'Times New Roman'
    style_normal.font.size = Pt(12)
    style_normal.font.color.rgb = RGBColor(0, 0, 0)
    
    def add_p(text, bold=False, italic=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=6):
        p = doc.add_paragraph()
        p.alignment = align
        p.paragraph_format.line_spacing = 1.5
        p.paragraph_format.space_after = Pt(space_after)
        run = p.add_run(text)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)
        run.bold = bold
        run.italic = italic
        return p

    def add_chap_head(num_str, title_str):
        doc.add_page_break()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(24)
        p.paragraph_format.space_after = Pt(18)
        r = p.add_run(f"CHAPTER {num_str}\n{title_str.upper()}")
        r.font.name = 'Times New Roman'
        r.font.size = Pt(15)
        r.bold = True
        r.font.color.rgb = RGBColor(15, 23, 42)

    def add_sec_head(text):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
        r = p.add_run(text)
        r.font.name = 'Times New Roman'
        r.font.size = Pt(13)
        r.bold = True
        r.font.color.rgb = RGBColor(30, 41, 59)

    def add_subsec_head(text):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        r = p.add_run(text)
        r.font.name = 'Times New Roman'
        r.font.size = Pt(12)
        r.bold = True
        r.font.color.rgb = RGBColor(51, 65, 85)

    # =========================================================================
    # 1. COVER PAGE & TITLE PAGE
    # =========================================================================
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(36)
    p.paragraph_format.space_after = Pt(12)
    p.paragraph_format.line_spacing = 1.3
    
    r = p.add_run("CRIMEWATCH:\nAUTONOMOUS REAL-TIME CCTV CRIME DETECTION AND EMERGENCY DISPATCH SYSTEM USING SPATIOTEMPORAL VISION TRANSFORMERS\n\n")
    r.bold = True
    r.font.size = Pt(16)
    r.font.color.rgb = RGBColor(15, 23, 42)
    
    r = p.add_run("A PROJECT REPORT\nSubmitted by\n\n")
    r.bold = True
    r.font.size = Pt(13)
    
    r = p.add_run("SANJAY CHAUHAN (Reg No: 21BCE0000)\n<CO-AUTHOR NAME> (Reg No: 21BCE0000)\n\n")
    r.bold = True
    r.font.size = Pt(13)
    r.font.color.rgb = RGBColor(30, 41, 59)
    
    r = p.add_run("In partial fulfillment for the award of the degree of\nBACHELOR OF TECHNOLOGY\nIN\nCOMPUTER SCIENCE AND ENGINEERING\n\n")
    r.font.size = Pt(12)
    
    r = p.add_run("Course Code & Name: CSE4001 - Project Exhibition\nSlot: EPJ - Review III Report Submission\n\nProject Guide:\n<Guide Name>\n<Designation>, SCOPE\n\n")
    r.font.size = Pt(12)
    
    r = p.add_run("SCHOOL OF COMPUTER SCIENCE AND ENGINEERING (SCOPE)\nVELLORE INSTITUTE OF TECHNOLOGY (VIT)\n(Deemed to be University under section 3 of UGC Act, 1956)\nVELLORE - 632014, TAMIL NADU, INDIA\n\nFALL SEMESTER 2026-27")
    r.bold = True
    r.font.size = Pt(12)

    # =========================================================================
    # 2. BONAFIDE CERTIFICATE
    # =========================================================================
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("BONAFIDE CERTIFICATE")
    r.bold = True
    r.font.size = Pt(15)

    add_p("Certified that this project report entitled 'CRIMEWATCH: AUTONOMOUS REAL-TIME CCTV CRIME DETECTION AND EMERGENCY DISPATCH SYSTEM USING SPATIOTEMPORAL VISION TRANSFORMERS' is the bonafide work of SANJAY CHAUHAN (Reg No: 21BCE0000) and <CO-AUTHOR NAME> (Reg No: 21BCE0000) who carried out the project work under my supervision during the Fall Semester 2026-27.")
    add_p("This report represents original work carried out by the candidates and has not been submitted in part or full for the award of any other degree, diploma, fellowship, or other similar title to this or any other University or Institution.")
    
    add_p("\n\n\n")
    p_sig = doc.add_paragraph()
    p_sig.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    r_sig1 = p_sig.add_run("Guide Signature: ______________________                  ")
    r_sig1.bold = True
    r_sig2 = p_sig.add_run("Head of the Department: ______________________")
    r_sig2.bold = True
    
    add_p("Name: <Guide Name>                                              Name: <HoD Name>")
    add_p("Designation: Assistant Professor / Associate Professor         School of Computer Science & Engineering")
    add_p("School of Computer Science & Engineering (SCOPE)               Vellore Institute of Technology (VIT)")
    add_p("Vellore Institute of Technology (VIT), Vellore                 Vellore - 632014, Tamil Nadu")

    # =========================================================================
    # 3. DECLARATION
    # =========================================================================
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("DECLARATION")
    r.bold = True
    r.font.size = Pt(15)

    add_p("We hereby declare that the project work entitled 'CRIMEWATCH: AUTONOMOUS REAL-TIME CCTV CRIME DETECTION AND EMERGENCY DISPATCH SYSTEM USING SPATIOTEMPORAL VISION TRANSFORMERS', submitted to the School of Computer Science and Engineering (SCOPE), Vellore Institute of Technology (VIT), Vellore, is a record of original research work done by us under the guidance of <Guide Name>, Designation, SCOPE, VIT Vellore.")
    add_p("We further declare that the matter embodied in this report has not been submitted elsewhere for the award of any other degree or diploma.")
    add_p("We also declare that all code, models, algorithms, experimental evaluations, and system integrations presented herein reflect our authentic contributions and that all external publications, toolkits, and literature sources have been appropriately cited as per standard IEEE academic conventions.")
    
    add_p("\n\n\n")
    add_p("Place: Vellore, Tamil Nadu                                       Candidate Signature: ______________________")
    add_p("Date:  02 November 2026                                         Candidate Name: SANJAY CHAUHAN")
    add_p("                                                                 Registration No: 21BCE0000")

    # =========================================================================
    # 4. ACKNOWLEDGEMENTS
    # =========================================================================
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("ACKNOWLEDGEMENTS")
    r.bold = True
    r.font.size = Pt(15)

    add_p("First and foremost, we express our profound gratitude to our Honorable Chancellor, Dr. G. Viswanathan, for providing an exemplary academic atmosphere and world-class computational infrastructure at Vellore Institute of Technology (VIT) that fostered the execution of this engineering project.")
    add_p("We extend our heartfelt gratitude to the Vice-Chancellor, Pro-Vice-Chancellors, and the Dean of the School of Computer Science and Engineering (SCOPE) for their visionary administrative support and continuous encouragement throughout our academic tenure.")
    add_p("We owe a deep debt of gratitude and respect to our project guide, <Guide Name>, <Designation>, School of Computer Science and Engineering, for their invaluable mentorship, insightful technical guidance, incisive critiques, and unwavering encouragement from the conceptualization stage to the completion of this research project.")
    add_p("We also thank all the faculty members of the School of Computer Science and Engineering, whose academic instruction provided the theoretical foundation required to tackle complex computer vision, deep learning, and distributed software engineering challenges.")
    add_p("Finally, we are deeply indebted to our parents, family members, and peers whose moral encouragement, understanding, and personal sacrifices provided us with the continuous energy and resilience required to accomplish this endeavor.")

    # =========================================================================
    # 5. ABSTRACT
    # =========================================================================
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("ABSTRACT")
    r.bold = True
    r.font.size = Pt(15)

    add_p("Modern urban closed-circuit television (CCTV) surveillance networks generate thousands of hours of continuous unstructured video footage every single day. In conventional security monitoring infrastructures, human operators are tasked with observing multiple split-screen monitors simultaneously. Empirical psychological research demonstrates that human operators experience significant vigilance fatigue after only twenty minutes of continuous monitoring, causing up to 90% of violent activities or anomalies to be missed. Although traditional computer vision systems have utilized 2D Convolutional Neural Networks (CNNs) with Long Short-Term Memory (LSTM) recurrent networks or 3D CNNs (C3D, I3D, X3D), these legacy approaches suffer from localized receptive fields, extreme computational overhead, and an inability to model long-range spatiotemporal dependencies.")
    add_p("To overcome these fundamental challenges, this project presents 'CrimeWatch', an autonomous, real-time crime detection and emergency dispatch platform powered by a fine-tuned Video Masked Autoencoder (VideoMAE-Base) Vision Transformer (ViT) architecture comprising approximately 86.2 million parameters. VideoMAE processes surveillance footage as sequences of spatiotemporal 3D tubelet patches across 16-frame sliding temporal windows at 224x224 RGB resolution. Through multi-head spatiotemporal self-attention across space and time, the network captures nuanced motion trajectories, distinguishing violent crimes from benign everyday movements.")
    add_p("To address real-world class imbalances and temporal prediction fluctuations, CrimeWatch introduces a Calibrated Average-Probability Temporal Voting algorithm and an anomaly event merging operator that groups contiguous anomalous windows into unified incidents and pinpoints the exact peak timestamp of violence. A three-tier false-alarm suppression engine—integrating confidence gating (0.70), persistence verification (3 consecutive windows), and alert cooldown timers (60 seconds)—guarantees operational reliability. When a high-severity crime is verified, the system automatically dispatches multi-channel alerts: responsive HTML emails with attached forensic JPEG snapshots via SMTP, and urgent SMS dispatches via Twilio REST APIs.")
    add_p("Evaluated across an independent, strictly held-out benchmark split of 70 real-world surveillance videos spanning seven distinct categories (Assault, Burglary, Fighting, Normal, Robbery, Shooting, Shoplifting), CrimeWatch achieves an overall Top-1 classification accuracy of 71.43% (50/70 correctly classified videos), outperforming 3D CNN baselines (34.92%) while maintaining an average inference latency of ~0.15 seconds per 16-frame window on local workstation GPU hardware.")
    add_p("Keywords: VideoMAE, Vision Transformer, CCTV Surveillance, Spatiotemporal Self-Attention, Anomaly Detection, Temporal Voting, Automated Emergency Dispatch.", bold=True)

    # =========================================================================
    # 6. TABLE OF CONTENTS (2 Pages)
    # =========================================================================
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("TABLE OF CONTENTS")
    r.bold = True
    r.font.size = Pt(15)

    toc_headers = ["CHAPTER NO.", "TITLE", "PAGE NO."]
    toc_data = [
        ["", "BONAFIDE CERTIFICATE", "ii"],
        ["", "DECLARATION", "iii"],
        ["", "ACKNOWLEDGEMENTS", "iv"],
        ["", "ABSTRACT", "v"],
        ["", "LIST OF TABLES", "viii"],
        ["", "LIST OF FIGURES", "ix"],
        ["", "LIST OF SYMBOLS & ABBREVIATIONS", "xi"],
        ["1.", "INTRODUCTION", "1"],
        ["1.1", "Background & Motivation in Urban Surveillance", "1"],
        ["1.2", "Evolution of Automated Video Surveillance", "3"],
        ["1.3", "Problem Statement", "5"],
        ["1.4", "Project Objectives", "6"],
        ["1.5", "Scope and Operational Boundaries", "7"],
        ["1.6", "Methodological Roadmap", "8"],
        ["1.7", "Organization of the Project Report", "9"],
        ["2.", "LITERATURE REVIEW", "10"],
        ["2.1", "Taxonomy of Video Anomaly & Action Recognition", "10"],
        ["2.2", "Review of Handcrafted & 2D Convolutional Approaches", "12"],
        ["2.3", "Review of 3D CNNs and Temporal Modeling Networks", "14"],
        ["2.4", "Comprehensive Review of Contemporary Papers (2023–2025)", "16"],
        ["2.4.1", "Paper 1: Spatiotemporal Hybrid Networks (IEEE 2023)", "16"],
        ["2.4.2", "Paper 2: Weakly Supervised Multi-Scale Networks (Elsevier 2024)", "17"],
        ["2.4.3", "Paper 3: Lightweight 3D CNNs for Edge Video (Springer 2024)", "19"],
        ["2.4.4", "Paper 4: Masked Video Autoencoders for Anomaly (IEEE 2024)", "20"],
        ["2.4.5", "Paper 5: Cloud-Native Surveillance Alerting (IEEE 2025)", "22"],
        ["2.4.6", "Paper 6: Multi-Modal Violence Detection (Elsevier 2024)", "23"],
        ["2.4.7", "Paper 7: Spatiotemporal Transformers for Urban Traffic (IEEE 2023)", "24"],
        ["2.4.8", "Paper 8: Privacy-Preserving Surveillance (Springer 2024)", "25"],
        ["2.4.9", "Paper 9: Contrastive Video Representation Learning (IEEE 2023)", "26"],
        ["2.4.10", "Paper 10: Low-Light Anomaly Recognition (Elsevier 2025)", "27"],
        ["2.5", "Comparative Analysis Matrix of Existing Literature", "28"],
        ["2.6", "Identified Research Gaps & Proposed Solutions", "30"],
        ["3.", "PROPOSED METHODOLOGY & THEORETICAL FORMULATION", "32"],
        ["3.1", "End-to-End System Architectural Pipeline", "32"],
        ["3.2", "Mathematical Formulation of VideoMAE Self-Attention", "34"],
        ["3.2.1", "Spatiotemporal 3D Tubelet Patch Tokenization", "34"],
        ["3.2.2", "3D Sinusoidal Spatiotemporal Positional Encodings", "36"],
        ["3.2.3", "Multi-Head Spatiotemporal Self-Attention Formulation", "37"],
        ["3.2.4", "Feed-Forward Network & Layer Normalization Dynamics", "39"],
        ["3.2.5", "Linear Classification Head & Optimization Objective", "40"],
        ["3.3", "Surveillance Dataset Preparation & Temporal Stride Sampling", "41"],
        ["3.4", "Calibrated Average-Probability Temporal Voting", "43"],
        ["3.5", "Temporal Anomaly Merging & Peak Moment Identification", "45"],
        ["3.6", "Three-Tier False-Alarm Suppression Engine", "47"],
        ["4.", "SYSTEM ARCHITECTURE & IMPLEMENTATION DETAILS", "49"],
        ["4.1", "Hardware and Software Environment Configuration", "49"],
        ["4.2", "Two-Phase VideoMAE Progressive Fine-Tuning Pipeline", "51"],
        ["4.2.1", "Phase 1: Head-Only Transfer Learning Protocol", "51"],
        ["4.2.2", "Phase 2: Full Backbone Fine-Tuning with Differential LRs", "53"],
        ["4.2.3", "Automatic Mixed Precision (FP16) & Gradient Accumulation", "54"],
        ["4.3", "Decord C++ Hardware-Accelerated Video Extraction", "56"],
        ["4.4", "FastAPI Asynchronous Microservice Architecture", "58"],
        ["4.5", "Interactive Web Interface & CCTV HUD Overlay Engine", "60"],
        ["4.6", "Multi-Channel Emergency Alert Dispatch Pipeline (SMTP/SMS)", "62"],
        ["4.7", "Relational Database Schema & Telemetry Auditing", "64"],
        ["4.8", "Security, Privacy & Data Integrity Safeguards", "66"],
        ["5.", "TESTING, EXPERIMENTAL RESULTS & PERFORMANCE EVALUATION", "68"],
        ["5.1", "Experimental Setup & Held-out Evaluation Benchmark", "68"],
        ["5.2", "Quantitative Evaluation: Complete Classification Report", "70"],
        ["5.3", "Detailed Confusion Matrix & Error Distribution Analysis", "72"],
        ["5.4", "10-Epoch Training & Validation Convergence Dynamics", "74"],
        ["5.5", "Ablation Study: VideoMAE vs. 3D CNN (X3D-M)", "76"],
        ["5.6", "Inference Latency & Real-Time Throughput Profile", "78"],
        ["5.7", "Empirical Validation of False-Alarm Suppression Gates", "80"],
        ["5.8", "Live Multi-Channel Alert Dispatch Verification", "81"],
        ["6.", "CONCLUSION, LIMITATIONS & FUTURE ENHANCEMENTS", "83"],
        ["6.1", "Summary of Technical Contributions", "83"],
        ["6.2", "System Limitations & Operational Constraints", "85"],
        ["6.3", "Ethical & Societal Impact Assessment", "86"],
        ["6.4", "Future Research & Engineering Directions", "87"],
        ["", "APPENDICES (Complete Unabridged Source Code)", "89"],
        ["App. A", "VideoMAE Fine-Tuning Pipeline (train_videomae.py)", "89"],
        ["App. B", "VideoMAE Standalone Inference Engine (inference_videomae.py)", "94"],
        ["App. C", "FastAPI Core Application Server (backend/main.py)", "99"],
        ["App. D", "Automated Emergency Alerting Microservice (backend/alerts.py)", "104"],
        ["App. E", "SQLAlchemy Database Models Schema (backend/models.py)", "108"],
        ["App. F", "Real-Time HTML5 CCTV HUD Canvas Renderer Engine", "110"],
        ["", "REFERENCES (IEEE Academic Format)", "113"],
    ]
    add_table_data(doc, toc_headers, toc_data)

    # =========================================================================
    # 7. LIST OF TABLES & LIST OF FIGURES
    # =========================================================================
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("LIST OF TABLES")
    r.bold = True
    r.font.size = Pt(15)

    lot_headers = ["TABLE NO.", "TITLE", "PAGE NO."]
    lot_data = [
        ["Table 2.1", "Comprehensive Literature Review & Gap Analysis Matrix", "28"],
        ["Table 3.1", "CrimeWatch Target Classes and Operational Severity Tiers", "42"],
        ["Table 3.2", "Empirical Class Sensitivity Multipliers and Tuning Rationale", "44"],
        ["Table 3.3", "False-Alarm Suppression Verification Gates Parameters", "48"],
        ["Table 4.1", "Hardware and Software Environment Configuration Specifications", "50"],
        ["Table 4.2", "VideoMAE Dual-Phase Fine-Tuning Hyperparameters", "55"],
        ["Table 4.3", "FastAPI Core REST API Endpoints Specification", "59"],
        ["Table 4.4", "Database Relational Table Schema and Field Constraints", "65"],
        ["Table 5.1", "Per-Class Classification Report for VideoMAE-Base Benchmark", "70"],
        ["Table 5.2", "Confusion Matrix Counts (70 Held-out Surveillance Videos)", "72"],
        ["Table 5.3", "Comparative Performance: VideoMAE-Base vs. PyTorchVideo X3D-M", "76"],
        ["Table 5.4", "End-to-End Latency Breakdown per 16-Frame Temporal Clip", "78"],
        ["Table 5.5", "False-Alarm Suppression Rejection Rate Validation Results", "80"],
    ]
    add_table_data(doc, lot_headers, lot_data)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("LIST OF FIGURES")
    r.bold = True
    r.font.size = Pt(15)

    lof_headers = ["FIGURE NO.", "TITLE", "PAGE NO."]
    lof_data = [
        ["Figure 3.1", "CrimeWatch End-to-End System Architecture Flow Diagram", "33"],
        ["Figure 3.2", "VideoMAE Spatiotemporal Tubelet Patch Tokenization Scheme", "35"],
        ["Figure 3.3", "Sliding Window Uniform Stride Sampling Scheme Across Video", "42"],
        ["Figure 3.4", "Temporal Anomaly Merging and Peak Incident Identification", "46"],
        ["Figure 4.1", "Dual-Phase Fine-Tuning Strategy (Head Frozen vs. Full Fine-Tuning)", "52"],
        ["Figure 4.2", "Decord Random-Access Seek vs. Sequential OpenCV I/O Timeline", "57"],
        ["Figure 4.3", "Relational Database Entity-Relationship (ER) Diagram", "64"],
        ["Figure 5.1", "High-Resolution Test Confusion Matrix (71.4% Overall Accuracy)", "73"],
        ["Figure 5.2", "10-Epoch Training and Validation Dynamics Convergence Curves", "75"],
        ["Figure 5.3", "CrimeWatch Web Dashboard with Real-Time CCTV HUD Overlay", "81"],
        ["Figure 5.4", "Automated HTML Forensic Security Email Alert with Evidence Snapshot", "82"],
    ]
    add_table_data(doc, lof_headers, lof_data)

    # =========================================================================
    # 8. LIST OF SYMBOLS & ABBREVIATIONS
    # =========================================================================
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("LIST OF SYMBOLS & ABBREVIATIONS")
    r.bold = True
    r.font.size = Pt(15)

    sym_headers = ["SYMBOL / ACRONYM", "DESCRIPTION / MEANING"]
    sym_data = [
        ["AI", "Artificial Intelligence"],
        ["AMP", "Automatic Mixed Precision (FP16 computation)"],
        ["API", "Application Programming Interface"],
        ["AUC", "Area Under the Receiver Operating Characteristic Curve"],
        ["B", "Physical Mini-Batch Size per GPU Forward Pass"],
        ["C", "Number of Color Channels (C = 3 for RGB)"],
        ["CCTV", "Closed-Circuit Television"],
        ["CNN", "Convolutional Neural Network"],
        ["CPU", "Central Processing Unit"],
        ["CUDA", "Compute Unified Device Architecture (NVIDIA parallel computing)"],
        ["cuDNN", "CUDA Deep Neural Network Library"],
        ["D", "Transformer Latent Embedding Dimension (D = 768)"],
        ["d_k", "Per-Head Query/Key Latent Dimension (d_k = D / h = 64)"],
        ["DLT", "Distributed Ledger Technology (TRAI SMS Compliance in India)"],
        ["FPS", "Frames Per Second"],
        ["F1", "F1-Score (Harmonic Mean of Precision and Recall)"],
        ["GPU", "Graphics Processing Unit"],
        ["GELU", "Gaussian Error Linear Unit Activation Function"],
        ["H, W", "Frame Spatial Dimensions (Height = 224, Width = 224)"],
        ["h", "Number of Multi-Head Self-Attention Heads (h = 12)"],
        ["HUD", "Heads-Up Display"],
        ["IEEE", "Institute of Electrical and Electronics Engineers"],
        ["JPEG", "Joint Photographic Experts Group (Image Format)"],
        ["JSON", "JavaScript Object Notation"],
        ["L", "Number of Vision Transformer Encoder Blocks (L = 12)"],
        ["LN", "Layer Normalization"],
        ["LR", "Learning Rate"],
        ["LSTM", "Long Short-Term Memory Recurrent Neural Network"],
        ["MAE", "Masked Autoencoder"],
        ["MHSA", "Multi-Head Self-Attention"],
        ["MIL", "Multiple Instance Learning"],
        ["MLP", "Multi-Layer Perceptron"],
        ["N_p", "Total Spatiotemporal Token Count (N_p = 1568)"],
        ["NLP", "Natural Language Processing"],
        ["ORM", "Object-Relational Mapping (SQLAlchemy)"],
        ["P", "Spatial Patch Dimension (P = 16 pixels)"],
        ["P_w(c)", "Softmax Probability of Class c at Window w"],
        ["Q, K, V", "Query, Key, and Value Projection Matrices in Self-Attention"],
        ["REST", "Representational State Transfer"],
        ["RGB", "Red, Green, Blue Color Space"],
        ["S_c", "Empirical Sensitivity Multiplier for Class c"],
        ["SMS", "Short Message Service"],
        ["SMTP", "Simple Mail Transfer Protocol"],
        ["SSL / TLS", "Secure Sockets Layer / Transport Layer Security"],
        ["STIP", "Space-Time Interest Points"],
        ["SVM", "Support Vector Machine"],
        ["T", "Number of Temporal Frames sampled per Clip (T = 16)"],
        ["t_p", "Temporal Tubelet Patch Dimension (t_p = 2 frames)"],
        ["TRAI", "Telecom Regulatory Authority of India"],
        ["UCF", "University of Central Florida (UCF-Crime Dataset)"],
        ["UI / UX", "User Interface / User Experience"],
        ["ViT", "Vision Transformer"],
        ["VRAM", "Video Random Access Memory"],
    ]
    add_table_data(doc, sym_headers, sym_data)

    print("Front matter added successfully. Now writing expanded chapters...")

    doc.save(str(OUTPUT_DOCX))
    print("Checkpoint saved")

if __name__ == "__main__":
    build_55_page_report()

