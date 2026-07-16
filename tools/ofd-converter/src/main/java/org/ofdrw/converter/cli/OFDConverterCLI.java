
package org.ofdrw.converter.cli;

import org.ofdrw.converter.GeneralConvertException;
import org.ofdrw.converter.export.*;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

public class OFDConverterCLI {

    public static void main(String[] args) {
        if (args.length < 2) {
            printUsage();
            System.exit(1);
        }

        String inputPath = args[0];
        String outputPath = args[1];
        
        String format = "image";
        if (args.length > 2) {
            format = args[2].toLowerCase();
        }

        Path ofdPath = Paths.get(inputPath);
        Path targetPath = Paths.get(outputPath);

        if (!Files.exists(ofdPath)) {
            System.err.println("Error: Input file not found - " + inputPath);
            System.exit(1);
        }

        try {
            convert(ofdPath, targetPath, format);
            System.out.println("Conversion completed successfully!");
            System.out.println("Output: " + targetPath.toAbsolutePath());
        } catch (GeneralConvertException | IOException e) {
            System.err.println("Conversion failed: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }

    private static void convert(Path ofdPath, Path targetPath, String format) throws GeneralConvertException, IOException {
        switch (format) {
            case "image", "img", "png" -> {
                Files.createDirectories(targetPath);
                try (ImageExporter exporter = new ImageExporter(ofdPath, targetPath, "PNG", 15d)) {
                    exporter.export();
                }
            }
            case "svg" -> {
                Files.createDirectories(targetPath);
                try (SVGExporter exporter = new SVGExporter(ofdPath, targetPath, 15d)) {
                    exporter.export();
                }
            }
            case "html" -> {
                Files.createDirectories(targetPath.getParent());
                try (HTMLExporter exporter = new HTMLExporter(ofdPath, targetPath)) {
                    exporter.export();
                }
            }
            case "text", "txt" -> {
                Files.createDirectories(targetPath.getParent());
                try (TextExporter exporter = new TextExporter(ofdPath, targetPath)) {
                    exporter.export();
                }
            }
            case "pdf" -> {
                Files.createDirectories(targetPath.getParent());
                try (OFDExporter exporter = new org.ofdrw.converter.export.PDFExporterIText(ofdPath, targetPath)) {
                    exporter.export();
                }
            }
            default -> {
                System.err.println("Unknown format: " + format);
                printUsage();
                System.exit(1);
            }
        }
    }

    private static void printUsage() {
        System.out.println("OFD Converter CLI - Convert OFD documents to other formats");
        System.out.println();
        System.out.println("Usage: java -jar ofd-converter-cli.jar <input-ofd-file> <output-path> [format]");
        System.out.println();
        System.out.println("Arguments:");
        System.out.println("  input-ofd-file   Path to the input OFD file");
        System.out.println("  output-path      Output path (directory for image/svg, file path for html/text)");
        System.out.println("  format           Optional output format (default: image)");
        System.out.println();
        System.out.println("Supported formats:");
        System.out.println("  image, img, png  - Export to PNG images (output as directory)");
        System.out.println("  svg              - Export to SVG files (output as directory)");
        System.out.println("  html             - Export to HTML file (output as file path)");
        System.out.println("  text, txt        - Export to text file (output as file path)");
        System.out.println("  pdf              - Export to PDF file (output as file path)");
        System.out.println();
        System.out.println("Examples:");
        System.out.println("  java -jar ofd-converter-cli.jar input.ofd ./output/");
        System.out.println("  java -jar ofd-converter-cli.jar input.ofd ./output/ svg");
        System.out.println("  java -jar ofd-converter-cli.jar input.ofd ./output.html html");
        System.out.println("  java -jar ofd-converter-cli.jar input.ofd ./output.txt text");
        System.out.println("  java -jar ofd-converter-cli.jar input.ofd ./output.pdf pdf");
    }
}
