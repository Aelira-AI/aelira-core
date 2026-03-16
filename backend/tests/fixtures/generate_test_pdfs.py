#!/usr/bin/env python3
"""
Generate synthetic PDF test fixtures for automated testing.

Creates 5 academic-style PDFs with varying complexity:
1. Academic paper (headings, paragraphs, images, equations)
2. Lecture notes (bullet points, multiple sections)
3. Lab manual (tables, procedures, warnings)
4. Textbook chapter (complex structure)
5. Simple syllabus (basic structure)
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import black, red, blue
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
    Preformatted,
)
from reportlab.lib.enums import TA_CENTER
from PIL import Image, ImageDraw, ImageFont
import os
from pathlib import Path


def create_temp_image(
    filename: str, width: int = 400, height: int = 300, text: str = "Chart"
):
    """Create a temporary image for testing."""
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # Draw a simple chart-like pattern
    draw.rectangle([50, 50, width - 50, height - 50], outline="black", width=2)
    draw.line([50, height // 2, width - 50, height // 2], fill="blue", width=2)
    draw.line([width // 2, 50, width // 2, height - 50], fill="blue", width=2)

    # Add text
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24
        )
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    draw.text(
        ((width - text_width) // 2, (height - text_height) // 2),
        text,
        fill="black",
        font=font,
    )

    img.save(filename)
    return filename


def generate_academic_paper():
    """Generate a synthetic academic paper PDF."""
    output_path = Path(__file__).parent / "pdfs" / "academic_paper.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter, topMargin=1 * inch, bottomMargin=1 * inch
    )
    story = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=18,
        textColor=black,
        spaceAfter=12,
        alignment=TA_CENTER,
    )

    heading1_style = ParagraphStyle(
        "CustomHeading1",
        parent=styles["Heading1"],
        fontSize=14,
        textColor=black,
        spaceAfter=10,
        spaceBefore=12,
    )

    # Title
    story.append(
        Paragraph(
            "The Impact of Machine Learning on Educational Accessibility", title_style
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Authors
    story.append(Paragraph("<i>Dr. Jane Smith, Prof. John Doe</i>", styles["Normal"]))
    story.append(
        Paragraph(
            "<i>Department of Computer Science, University of Examples</i>",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.3 * inch))

    # Abstract
    story.append(Paragraph("Abstract", heading1_style))
    story.append(
        Paragraph(
            "This paper explores the application of machine learning techniques to improve "
            "educational accessibility for students with disabilities. We examine three key areas: "
            "automated caption generation, document remediation, and adaptive learning interfaces. "
            "Our findings demonstrate a 40% improvement in content accessibility metrics when "
            "AI-powered tools are deployed alongside traditional accessibility solutions.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Introduction
    story.append(Paragraph("1. Introduction", heading1_style))
    story.append(
        Paragraph(
            "Educational institutions face increasing pressure to provide accessible content "
            "for all students. The Americans with Disabilities Act (ADA) Title II compliance "
            "deadline of April 2026 has accelerated the need for scalable solutions. Traditional "
            "manual remediation processes are time-consuming and expensive, costing universities "
            "an estimated $3,000-$6,000 per course.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        Paragraph(
            "Recent advances in artificial intelligence offer promising alternatives. This paper "
            "presents empirical evidence from a 12-month study across five universities, examining "
            "the effectiveness of AI-powered accessibility tools.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Create temp image
    img_path = "/tmp/test_chart.png"
    create_temp_image(img_path, text="Research Results")

    # Figure
    story.append(
        Paragraph("Figure 1: Accessibility Improvement Metrics", styles["Normal"])
    )
    story.append(RLImage(img_path, width=4 * inch, height=3 * inch))
    story.append(Spacer(1, 0.2 * inch))

    # Methodology
    story.append(Paragraph("2. Methodology", heading1_style))
    story.append(Paragraph("2.1 Data Collection", styles["Heading2"]))
    story.append(
        Paragraph(
            "We collected 5,000 educational documents across five universities, including PDFs, "
            "PowerPoint presentations, and LaTeX documents. Each document was evaluated using "
            "both manual review and automated scanning tools.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))

    # Table
    story.append(
        Paragraph("Table 1: Document Types and Sample Sizes", styles["Normal"])
    )
    data = [
        ["Document Type", "Sample Size", "Avg. Pages", "Issues Found"],
        ["PDF Documents", "2,000", "15.3", "1,247"],
        ["PowerPoint Decks", "1,500", "25.7", "892"],
        ["LaTeX Papers", "1,000", "12.1", "456"],
        ["Word Documents", "500", "8.5", "234"],
    ]
    table = Table(data, colWidths=[2 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("TEXTCOLOR", (0, 0), (-1, 0), black),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("GRID", (0, 0), (-1, -1), 1, black),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Results
    story.append(Paragraph("3. Results", heading1_style))
    story.append(
        Paragraph(
            "Our analysis revealed significant improvements in accessibility metrics when "
            "AI-powered tools were employed. The average compliance score increased from "
            "62% (manual process) to 87% (AI-assisted process).",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Conclusion
    story.append(Paragraph("4. Conclusion", heading1_style))
    story.append(
        Paragraph(
            "This study demonstrates that machine learning techniques can substantially improve "
            "educational accessibility outcomes. While human oversight remains essential, "
            "AI-powered tools can automate 80-90% of remediation work, reducing costs and "
            "improving consistency.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # References
    story.append(Paragraph("References", heading1_style))
    story.append(
        Paragraph(
            "[1] Smith, J. et al. (2024). Automated Accessibility Testing. <i>Journal of Educational Technology</i>.",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "[2] Doe, J. (2023). AI in Higher Education. <i>ACM Computing Surveys</i>.",
            styles["Normal"],
        )
    )

    # Build PDF
    doc.build(story)
    print(f"✅ Generated: {output_path}")

    # Cleanup
    if os.path.exists(img_path):
        os.remove(img_path)


def generate_lecture_notes():
    """Generate synthetic lecture notes PDF."""
    output_path = Path(__file__).parent / "pdfs" / "lecture_notes.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    story = []
    styles = getSampleStyleSheet()

    # Title
    story.append(Paragraph("CS 301: Data Structures and Algorithms", styles["Title"]))
    story.append(Paragraph("Lecture 5: Binary Search Trees", styles["Heading1"]))
    story.append(Paragraph("<i>Fall 2025 - Prof. Johnson</i>", styles["Normal"]))
    story.append(Spacer(1, 0.3 * inch))

    # Learning objectives
    story.append(Paragraph("Learning Objectives:", styles["Heading2"]))
    story.append(
        Paragraph("• Understand binary search tree (BST) properties", styles["Normal"])
    )
    story.append(
        Paragraph("• Implement BST insertion and deletion operations", styles["Normal"])
    )
    story.append(
        Paragraph("• Analyze time complexity of BST operations", styles["Normal"])
    )
    story.append(
        Paragraph("• Compare BSTs with balanced tree structures", styles["Normal"])
    )
    story.append(Spacer(1, 0.2 * inch))

    # Section 1
    story.append(Paragraph("1. Binary Search Tree Definition", styles["Heading2"]))
    story.append(
        Paragraph(
            "A binary search tree is a binary tree data structure with the following properties:",
            styles["BodyText"],
        )
    )
    story.append(
        Paragraph(
            "• Left subtree contains only nodes with keys less than parent",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "• Right subtree contains only nodes with keys greater than parent",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph("• Both subtrees must also be binary search trees", styles["Normal"])
    )
    story.append(Spacer(1, 0.2 * inch))

    # Code example
    story.append(Paragraph("Example: BST Node Structure", styles["Heading3"]))
    code = """class TreeNode:
    def __init__(self, val=0):
        self.val = val
        self.left = None
        self.right = None

def insert(root, val):
    if not root:
        return TreeNode(val)
    if val < root.val:
        root.left = insert(root.left, val)
    else:
        root.right = insert(root.right, val)
    return root"""
    story.append(Preformatted(code, styles["Code"]))
    story.append(Spacer(1, 0.2 * inch))

    # Section 2
    story.append(Paragraph("2. Time Complexity Analysis", styles["Heading2"]))
    story.append(
        Paragraph(
            "The time complexity of BST operations depends on the tree's height:",
            styles["BodyText"],
        )
    )
    data = [
        ["Operation", "Average Case", "Worst Case"],
        ["Search", "O(log n)", "O(n)"],
        ["Insert", "O(log n)", "O(n)"],
        ["Delete", "O(log n)", "O(n)"],
    ]
    table = Table(data, colWidths=[2 * inch, 1.5 * inch, 1.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, black),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Practice problems
    story.append(Paragraph("3. Practice Problems", styles["Heading2"]))
    story.append(
        Paragraph(
            "Problem 1: Insert the following values into an empty BST: 50, 30, 70, 20, 40, 60, 80",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "Problem 2: What is the time complexity of finding the minimum element in a BST?",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "Problem 3: Implement an in-order traversal function for a BST",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.3 * inch))

    # Next lecture
    story.append(
        Paragraph("Next Lecture: AVL Trees and Tree Balancing", styles["Heading2"])
    )
    story.append(Paragraph("Reading: Chapter 12, sections 12.1-12.3", styles["Normal"]))

    doc.build(story)
    print(f"✅ Generated: {output_path}")


def generate_lab_manual():
    """Generate synthetic lab manual PDF."""
    output_path = Path(__file__).parent / "pdfs" / "lab_manual.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter, topMargin=1 * inch, bottomMargin=1 * inch
    )
    story = []
    styles = getSampleStyleSheet()

    # Custom warning style
    warning_style = ParagraphStyle(
        "Warning", parent=styles["Normal"], textColor=red, fontSize=11, spaceAfter=10
    )

    # Title
    story.append(Paragraph("Chemistry 101 Laboratory Manual", styles["Title"]))
    story.append(Paragraph("Experiment 3: Acid-Base Titration", styles["Heading1"]))
    story.append(Spacer(1, 0.3 * inch))

    # Safety warning
    story.append(Paragraph("⚠️ SAFETY WARNING", styles["Heading2"]))
    story.append(
        Paragraph(
            "This experiment involves the use of strong acids and bases. Always wear safety goggles, "
            "lab coat, and gloves. Work in a well-ventilated area or fume hood.",
            warning_style,
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Objectives
    story.append(Paragraph("Objectives", styles["Heading2"]))
    story.append(
        Paragraph(
            "1. Determine the concentration of an unknown acid solution",
            styles["Normal"],
        )
    )
    story.append(Paragraph("2. Practice proper titration technique", styles["Normal"]))
    story.append(
        Paragraph("3. Calculate molarity from titration data", styles["Normal"])
    )
    story.append(Spacer(1, 0.2 * inch))

    # Materials
    story.append(Paragraph("Materials", styles["Heading2"]))
    data = [
        ["Equipment", "Quantity", "Chemicals", "Amount"],
        ["Burette", "1", "0.1 M NaOH", "50 mL"],
        ["Erlenmeyer flask", "3", "Unknown HCl", "25 mL each"],
        ["Pipette (25 mL)", "1", "Phenolphthalein", "3 drops"],
        ["Burette clamp", "1", "Distilled water", "As needed"],
    ]
    table = Table(data, colWidths=[2 * inch, 1 * inch, 2 * inch, 1.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Procedure
    story.append(Paragraph("Procedure", styles["Heading2"]))
    story.append(Paragraph("Step 1: Prepare the burette", styles["Heading3"]))
    story.append(
        Paragraph(
            "Rinse the burette with distilled water, then with small amounts of the NaOH solution. "
            "Fill the burette with 0.1 M NaOH solution. Record the initial volume.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("Step 2: Prepare the flask", styles["Heading3"]))
    story.append(
        Paragraph(
            "Use a pipette to transfer exactly 25.0 mL of unknown HCl solution into an Erlenmeyer flask. "
            "Add 2-3 drops of phenolphthalein indicator.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("Step 3: Perform titration", styles["Heading3"]))
    story.append(
        Paragraph(
            "Slowly add NaOH from the burette while swirling the flask constantly. As you approach "
            "the endpoint, add drops more slowly. Stop when the solution turns pale pink and remains "
            "that color for 30 seconds. Record the final volume.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("Step 4: Repeat", styles["Heading3"]))
    story.append(
        Paragraph(
            "Repeat the titration two more times for a total of three trials. Average the volumes used.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Data table
    story.append(Paragraph("Data Collection", styles["Heading2"]))
    data = [
        ["Trial", "Initial Volume (mL)", "Final Volume (mL)", "Volume Used (mL)"],
        ["1", "", "", ""],
        ["2", "", "", ""],
        ["3", "", "", ""],
        ["Average", "", "", ""],
    ]
    table = Table(data, colWidths=[1 * inch, 2 * inch, 2 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, black),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Calculations
    story.append(Paragraph("Calculations", styles["Heading2"]))
    story.append(Paragraph("Use the formula: M₁V₁ = M₂V₂", styles["Normal"]))
    story.append(
        Paragraph(
            "Where M₁ = molarity of HCl (unknown), V₁ = 25.0 mL, M₂ = 0.1 M (NaOH), V₂ = average volume used",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Questions
    story.append(Paragraph("Post-Lab Questions", styles["Heading2"]))
    story.append(
        Paragraph(
            "1. Why is it important to rinse the burette with NaOH solution before filling?",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "2. What would happen if you added too much NaOH past the endpoint?",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "3. Calculate the percent error if the actual concentration was 0.105 M.",
            styles["Normal"],
        )
    )

    doc.build(story)
    print(f"✅ Generated: {output_path}")


def generate_textbook_chapter():
    """Generate synthetic textbook chapter PDF."""
    output_path = Path(__file__).parent / "pdfs" / "textbook_chapter.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter, topMargin=1 * inch, bottomMargin=1 * inch
    )
    story = []
    styles = getSampleStyleSheet()

    # Chapter title
    story.append(Paragraph("Chapter 7", styles["Title"]))
    story.append(Paragraph("Neural Networks and Deep Learning", styles["Heading1"]))
    story.append(Spacer(1, 0.4 * inch))

    # Learning outcomes box
    story.append(Paragraph("Learning Outcomes", styles["Heading2"]))
    story.append(
        Paragraph(
            "After completing this chapter, you will be able to:", styles["Normal"]
        )
    )
    story.append(
        Paragraph(
            "• Explain the architecture of artificial neural networks", styles["Normal"]
        )
    )
    story.append(
        Paragraph("• Implement forward and backward propagation", styles["Normal"])
    )
    story.append(
        Paragraph("• Apply activation functions appropriately", styles["Normal"])
    )
    story.append(
        Paragraph("• Train neural networks using gradient descent", styles["Normal"])
    )
    story.append(Spacer(1, 0.3 * inch))

    # Section 7.1
    story.append(Paragraph("7.1 Introduction to Neural Networks", styles["Heading2"]))
    story.append(
        Paragraph(
            "Artificial neural networks are computational models inspired by biological neural systems. "
            "They consist of interconnected nodes (neurons) organized in layers, each performing simple "
            "computations that collectively enable complex pattern recognition and decision-making.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("7.1.1 The Perceptron", styles["Heading3"]))
    story.append(
        Paragraph(
            "The simplest neural network is the perceptron, introduced by Frank Rosenblatt in 1958. "
            "A perceptron takes multiple inputs, applies weights, and produces a single binary output:",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))

    # Example box
    story.append(
        Paragraph("<b>Example 7.1:</b> Binary Classification", styles["Normal"])
    )
    story.append(
        Paragraph(
            "Consider a perceptron with two inputs x₁ and x₂, weights w₁ = 0.5 and w₂ = 0.3, "
            "and bias b = -0.2. The output is computed as:",
            styles["Normal"],
        )
    )
    story.append(Paragraph("y = step(w₁x₁ + w₂x₂ + b)", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    # Section 7.2
    story.append(Paragraph("7.2 Multi-Layer Networks", styles["Heading2"]))
    story.append(
        Paragraph(
            "While perceptrons can solve linearly separable problems, many real-world tasks require "
            "multi-layer architectures. A typical feedforward neural network consists of:",
            styles["BodyText"],
        )
    )
    story.append(Paragraph("• <b>Input layer:</b> Receives raw data", styles["Normal"]))
    story.append(
        Paragraph(
            "• <b>Hidden layers:</b> Extract features and patterns", styles["Normal"]
        )
    )
    story.append(
        Paragraph("• <b>Output layer:</b> Produces final predictions", styles["Normal"])
    )
    story.append(Spacer(1, 0.2 * inch))

    # Create diagram
    img_path = "/tmp/nn_diagram.png"
    create_temp_image(
        img_path, width=500, height=350, text="Neural Network Architecture"
    )
    story.append(
        Paragraph(
            "Figure 7.1: Three-layer neural network architecture", styles["Normal"]
        )
    )
    story.append(RLImage(img_path, width=4.5 * inch, height=3 * inch))
    story.append(Spacer(1, 0.2 * inch))

    # Section 7.3
    story.append(Paragraph("7.3 Activation Functions", styles["Heading2"]))
    story.append(
        Paragraph(
            "Activation functions introduce non-linearity into the network, enabling it to learn "
            "complex patterns. Common activation functions include:",
            styles["BodyText"],
        )
    )

    # Table of activation functions
    data = [
        ["Function", "Formula", "Range", "Use Case"],
        ["Sigmoid", "σ(x) = 1/(1+e⁻ˣ)", "(0, 1)", "Binary classification"],
        ["Tanh", "tanh(x)", "(-1, 1)", "Hidden layers"],
        ["ReLU", "max(0, x)", "[0, ∞)", "Most hidden layers"],
        ["Softmax", "eˣⁱ / Σeˣʲ", "(0, 1)", "Multi-class output"],
    ]
    table = Table(data, colWidths=[1.5 * inch, 1.8 * inch, 1.2 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Key concepts box
    story.append(Paragraph("Key Concepts", styles["Heading2"]))
    story.append(
        Paragraph(
            "• <b>Forward propagation:</b> Computing outputs from inputs",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "• <b>Backpropagation:</b> Computing gradients for weight updates",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "• <b>Gradient descent:</b> Optimization algorithm for training",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "• <b>Learning rate:</b> Controls step size during optimization",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.3 * inch))

    # Summary
    story.append(Paragraph("Chapter Summary", styles["Heading2"]))
    story.append(
        Paragraph(
            "Neural networks are powerful models capable of learning complex patterns from data. "
            "By stacking multiple layers and using appropriate activation functions, they can "
            "approximate virtually any function. The training process uses backpropagation and "
            "gradient descent to iteratively improve the network's parameters.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Exercises
    story.append(Paragraph("Exercises", styles["Heading2"]))
    story.append(
        Paragraph("1. Implement a single-layer perceptron in Python", styles["Normal"])
    )
    story.append(
        Paragraph(
            "2. Explain why linear activation functions are not useful in hidden layers",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "3. Calculate the number of parameters in a network with layers [784, 128, 64, 10]",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "4. Research and explain the vanishing gradient problem", styles["Normal"]
        )
    )

    doc.build(story)
    print(f"✅ Generated: {output_path}")

    # Cleanup
    if os.path.exists(img_path):
        os.remove(img_path)


def generate_simple_syllabus():
    """Generate simple syllabus PDF."""
    output_path = Path(__file__).parent / "pdfs" / "simple_syllabus.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter, topMargin=1 * inch, bottomMargin=1 * inch
    )
    story = []
    styles = getSampleStyleSheet()

    # Title
    story.append(Paragraph("MATH 250: Introduction to Statistics", styles["Title"]))
    story.append(Paragraph("Fall 2025 Syllabus", styles["Heading1"]))
    story.append(Spacer(1, 0.3 * inch))

    # Instructor info
    story.append(Paragraph("Instructor Information", styles["Heading2"]))
    story.append(Paragraph("<b>Professor:</b> Dr. Emily Chen", styles["Normal"]))
    story.append(Paragraph("<b>Email:</b> e.chen@university.edu", styles["Normal"]))
    story.append(
        Paragraph("<b>Office:</b> Mathematics Building, Room 312", styles["Normal"])
    )
    story.append(
        Paragraph(
            "<b>Office Hours:</b> Tuesday/Thursday 2:00-4:00 PM", styles["Normal"]
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Course info
    story.append(Paragraph("Course Information", styles["Heading2"]))
    story.append(
        Paragraph("<b>Meeting Times:</b> MWF 10:00-10:50 AM", styles["Normal"])
    )
    story.append(Paragraph("<b>Location:</b> Science Hall, Room 204", styles["Normal"]))
    story.append(Paragraph("<b>Credits:</b> 3 semester hours", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    # Description
    story.append(Paragraph("Course Description", styles["Heading2"]))
    story.append(
        Paragraph(
            "This course provides an introduction to statistical concepts and methods. Topics include "
            "descriptive statistics, probability distributions, hypothesis testing, confidence intervals, "
            "correlation, and regression. Emphasis is placed on practical applications and interpretation "
            "of results.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Learning outcomes
    story.append(Paragraph("Learning Outcomes", styles["Heading2"]))
    story.append(
        Paragraph(
            "Upon successful completion of this course, students will be able to:",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph("1. Calculate and interpret descriptive statistics", styles["Normal"])
    )
    story.append(
        Paragraph(
            "2. Apply probability theory to solve real-world problems", styles["Normal"]
        )
    )
    story.append(
        Paragraph(
            "3. Conduct hypothesis tests and construct confidence intervals",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph("4. Perform correlation and regression analysis", styles["Normal"])
    )
    story.append(Spacer(1, 0.2 * inch))

    # Required materials
    story.append(Paragraph("Required Materials", styles["Heading2"]))
    story.append(
        Paragraph(
            "• <b>Textbook:</b> <i>Statistics for Data Science</i> by Johnson & Lee (3rd ed.)",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph("• <b>Calculator:</b> TI-84 or equivalent", styles["Normal"])
    )
    story.append(
        Paragraph(
            "• <b>Software:</b> R or Python (free, instructions provided)",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Grading
    story.append(Paragraph("Grading Policy", styles["Heading2"]))
    data = [
        ["Component", "Weight", "Description"],
        ["Homework", "20%", "Weekly assignments (10 total, lowest dropped)"],
        ["Quizzes", "15%", "Short quizzes every two weeks (5 total)"],
        ["Midterm Exam", "25%", "Covers chapters 1-6"],
        ["Final Exam", "30%", "Comprehensive"],
        ["Participation", "10%", "Attendance and in-class activities"],
    ]
    table = Table(data, colWidths=[1.8 * inch, 1.2 * inch, 3.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("ALIGN", (0, 0), (1, -1), "LEFT"),
                ("ALIGN", (2, 0), (2, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, black),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Letter grades
    story.append(Paragraph("Letter Grade Scale:", styles["Normal"]))
    story.append(
        Paragraph(
            "A: 90-100% | B: 80-89% | C: 70-79% | D: 60-69% | F: Below 60%",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Course schedule
    story.append(Paragraph("Course Schedule (Tentative)", styles["Heading2"]))
    data = [
        ["Week", "Topics", "Assignments"],
        ["1-2", "Descriptive Statistics, Data Visualization", "HW 1"],
        ["3-4", "Probability Basics, Distributions", "HW 2, Quiz 1"],
        ["5-6", "Normal Distribution, Sampling", "HW 3"],
        ["7-8", "Confidence Intervals, Hypothesis Testing", "HW 4, Quiz 2, Midterm"],
        ["9-10", "t-tests, ANOVA", "HW 5, Quiz 3"],
        ["11-12", "Chi-square Tests, Correlation", "HW 6"],
        ["13-14", "Linear Regression, Multiple Regression", "HW 7, Quiz 4"],
        ["15", "Review and Practice", "Quiz 5"],
        ["16", "Final Exam", "Final Exam"],
    ]
    table = Table(data, colWidths=[1 * inch, 3.5 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Policies
    story.append(Paragraph("Course Policies", styles["Heading2"]))
    story.append(
        Paragraph(
            "<b>Attendance:</b> Regular attendance is expected. More than 3 absences may affect your grade.",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "<b>Late Work:</b> Late homework is accepted within 48 hours with a 20% penalty.",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            "<b>Academic Integrity:</b> All work must be your own. Violations will result in disciplinary action.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Accessibility
    story.append(Paragraph("Accessibility Statement", styles["Heading2"]))
    story.append(
        Paragraph(
            "Students with disabilities who need accommodations should contact the Office of Disability "
            "Services and provide documentation. I am committed to ensuring all students have equal access "
            "to course materials and activities.",
            styles["BodyText"],
        )
    )

    doc.build(story)
    print(f"✅ Generated: {output_path}")


def main():
    """Generate all test PDF fixtures."""
    print("🔄 Generating PDF test fixtures...\n")

    generate_academic_paper()
    generate_lecture_notes()
    generate_lab_manual()
    generate_textbook_chapter()
    generate_simple_syllabus()

    print("\n✅ All PDF fixtures generated successfully!")
    print(f"📁 Location: {Path(__file__).parent / 'pdfs'}")


if __name__ == "__main__":
    main()
