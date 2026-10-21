// tabula-java の表抽出をページ番号つきの JSON で書き出す（jdocbench の tabula アダプタ用）。
//
// tabula-java の CLI（`-f JSON -p all`）の JSON には表のページ番号が無いため、CLI の
// CommandLineApp.extractFile と同じ手順（PDDocument.load → ObjectExtractor.extract → ページごとに
// lattice は SpreadsheetExtractionAlgorithm、stream は BasicExtractionAlgorithm）をライブラリから呼び、
// ページごとに CLI と同じ JSONWriter の出力（表の配列）を `{"page": N, "tables": [...]}` で包む。
// 表の無いページも空配列で残す。表の中身は加工しない。
//
// 実行: java -cp tabula-<ver>-jar-with-dependencies.jar TabulaPages.java <pdf> <lattice|stream> <out.json>
// （Java 11 以上の単一ソースファイル実行。JDK が必要）

import java.io.File;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;

import org.apache.pdfbox.pdmodel.PDDocument;

import technology.tabula.ObjectExtractor;
import technology.tabula.Page;
import technology.tabula.PageIterator;
import technology.tabula.Table;
import technology.tabula.extractors.BasicExtractionAlgorithm;
import technology.tabula.extractors.ExtractionAlgorithm;
import technology.tabula.extractors.SpreadsheetExtractionAlgorithm;
import technology.tabula.writers.JSONWriter;

public class TabulaPages {
    public static void main(String[] args) throws Exception {
        String mode = args[1];
        ExtractionAlgorithm algorithm;
        if (mode.equals("lattice")) {
            algorithm = new SpreadsheetExtractionAlgorithm();
        } else if (mode.equals("stream")) {
            algorithm = new BasicExtractionAlgorithm();
        } else {
            throw new IllegalArgumentException("mode は lattice か stream: " + mode);
        }
        JSONWriter json = new JSONWriter();
        try (PDDocument document = PDDocument.load(new File(args[0]));
                Writer out = new OutputStreamWriter(
                        Files.newOutputStream(new File(args[2]).toPath()), StandardCharsets.UTF_8)) {
            ObjectExtractor extractor = new ObjectExtractor(document);
            PageIterator pages = extractor.extract();
            out.append("{\"mode\":\"").append(mode).append("\",\"pages\":[");
            boolean first = true;
            while (pages.hasNext()) {
                Page page = pages.next();
                @SuppressWarnings("unchecked")
                List<Table> tables = (List<Table>) algorithm.extract(page);
                if (!first) {
                    out.append(",");
                }
                first = false;
                out.append("{\"page\":").append(String.valueOf(page.getPageNumber()));
                out.append(",\"tables\":");
                json.write(out, tables);
                out.append("}");
            }
            out.append("]}");
        }
    }
}
