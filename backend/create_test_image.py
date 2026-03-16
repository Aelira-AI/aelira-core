#!/usr/bin/env python3
"""Create a simple test image for image alt text generation."""

from PIL import Image, ImageDraw, ImageFont

# Create a sample chart image
img = Image.new('RGB', (800, 600), color='white')
draw = ImageDraw.Draw(img)

# Draw bars for a simple bar chart
colors = ['#4285F4', '#34A853', '#FBBC05', '#EA4335']
labels = ['Q1', 'Q2', 'Q3', 'Q4']
values = [65, 80, 55, 90]

bar_width = 100
spacing = 50
start_x = 150
baseline = 500

# Draw title
draw.text((250, 30), "Quarterly Revenue ($M)", fill='black')

# Draw bars
for i, (label, value, color) in enumerate(zip(labels, values, colors)):
    x = start_x + i * (bar_width + spacing)
    bar_height = value * 4
    y = baseline - bar_height

    # Draw bar
    draw.rectangle([x, y, x + bar_width, baseline], fill=color)

    # Draw label
    draw.text((x + 30, baseline + 20), label, fill='black')

    # Draw value
    draw.text((x + 35, y - 25), str(value), fill='black')

# Draw axes
draw.line([(100, baseline), (700, baseline)], fill='black', width=2)  # X-axis
draw.line([(100, 100), (100, baseline)], fill='black', width=2)  # Y-axis

# Save
img.save('tests/fixtures/sample_chart.png')
print("✅ Created sample_chart.png")

# Create a simple diagram
img2 = Image.new('RGB', (600, 400), color='white')
draw2 = ImageDraw.Draw(img2)

# Draw a simple flowchart
draw2.text((200, 30), "Simple Process Diagram", fill='black')

# Box 1
draw2.rectangle([200, 100, 400, 150], outline='#4285F4', width=2)
draw2.text((250, 115), "Start Process", fill='black')

# Arrow
draw2.line([(300, 150), (300, 200)], fill='black', width=2)
draw2.polygon([(300, 200), (295, 190), (305, 190)], fill='black')

# Box 2
draw2.rectangle([200, 200, 400, 250], outline='#34A853', width=2)
draw2.text((250, 215), "Execute Task", fill='black')

# Arrow
draw2.line([(300, 250), (300, 300)], fill='black', width=2)
draw2.polygon([(300, 300), (295, 290), (305, 290)], fill='black')

# Box 3
draw2.rectangle([200, 300, 400, 350], outline='#EA4335', width=2)
draw2.text((265, 315), "Complete", fill='black')

img2.save('tests/fixtures/sample_diagram.png')
print("✅ Created sample_diagram.png")

print("\n📊 Test images created in tests/fixtures/")
print("Run: python3 verify_llava.py to test llava model")
