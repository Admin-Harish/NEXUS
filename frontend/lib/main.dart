import 'dart:async';
import 'dart:convert';
import 'dart:js_interop';
import 'dart:typed_data';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:web/web.dart' as web;
import 'config.dart';

void main() => runApp(const NexusApp());

const brand = Color(0xff2457d6);
const okColor = Color(0xff14804a);
const badColor = Color(0xffc0392b);
const warnColor = Color(0xff9a6700);

class NexusApp extends StatelessWidget {
  const NexusApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'NEXUS',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: brand, scaffoldBackgroundColor: const Color(0xfff5f7fb)),
      home: const NexusHome(),
    );
  }
}

enum Stage { input, analyze, approve, execute, report }

const stageLabels = {
  Stage.input: 'Input',
  Stage.analyze: 'Analyze',
  Stage.approve: 'Approve plan',
  Stage.execute: 'Deploy & test',
  Stage.report: 'Report',
};

enum Mode { discover, scenario, excel }

const sampleRepos = {
  'Project1 · Volume API': 'https://github.com/Admin-Harish/Project1.git',
  'Project2 · Inventory API': 'https://github.com/Admin-Harish/Project2.git',
};

class NexusHome extends StatefulWidget {
  const NexusHome({super.key});
  @override
  State<NexusHome> createState() => _NexusHomeState();
}

class _NexusHomeState extends State<NexusHome> {
  final repoUrl = TextEditingController();
  final scenario = TextEditingController();
  final email = TextEditingController();
  Mode mode = Mode.discover;
  PlatformFile? excelFile;
  Set<String> selected = {};

  Stage stage = Stage.input;
  String analyzeStatus = '';
  String? error;
  bool busy = false;
  Map<String, dynamic>? project;
  Map<String, dynamic>? plan;
  Map<String, dynamic>? run;
  Timer? poller;
  int session = 0; // bumped on Back to home so late responses from an abandoned flow are ignored

  @override
  void dispose() {
    poller?.cancel();
    super.dispose();
  }

  // ---------- API ----------

  Future<dynamic> api(String method, String path, [Object? body]) async {
    final uri = Uri.parse('$apiBaseUrl$path');
    final headers = {'Content-Type': 'application/json'};
    final response = method == 'POST'
        ? await http.post(uri, headers: headers, body: jsonEncode(body ?? {}))
        : await http.get(uri, headers: headers);
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode >= 400) throw Exception(decoded is Map ? decoded['detail'] ?? response.body : response.body);
    return decoded;
  }

  Future<Map<String, dynamic>> uploadExcel(PlatformFile file) async {
    final request = http.MultipartRequest('POST', Uri.parse('$apiBaseUrl/api/projects/ingest-excel'));
    request.fields['url'] = repoUrl.text.trim();
    request.files.add(http.MultipartFile.fromBytes('file', file.bytes as Uint8List, filename: file.name));
    final response = await request.send();
    final body = jsonDecode(await response.stream.bytesToString());
    if (response.statusCode >= 400) throw Exception(body['detail'] ?? 'Upload failed');
    return body;
  }

  // ---------- Flow ----------

  bool get inputValid =>
      repoUrl.text.trim().isNotEmpty &&
      switch (mode) {
        Mode.discover => true,
        Mode.scenario => scenario.text.trim().isNotEmpty,
        Mode.excel => excelFile != null,
      };

  Future<void> startAnalysis() async {
    final mySession = session;
    setState(() {
      stage = Stage.analyze;
      error = null;
      analyzeStatus = mode == Mode.excel ? 'Reading your spreadsheet and analyzing the repository…' : 'Cloning and analyzing the repository…';
    });
    try {
      final p = mode == Mode.excel
          ? await uploadExcel(excelFile!)
          : await api('POST', '/api/projects/ingest', {'url': repoUrl.text.trim(), 'scenario': mode == Mode.scenario ? scenario.text.trim() : ''});
      if (mySession != session) return;
      setState(() {
        project = Map<String, dynamic>.from(p);
        analyzeStatus = switch (mode) {
          Mode.discover => 'Discovering test cases with GPT…',
          Mode.scenario => 'Designing test cases for your scenario with GPT…',
          Mode.excel => 'Mapping your rows to test cases and looking for gaps with GPT…',
        };
      });
      final generated = await api('POST', '/api/plans', {'project_id': project!['id']});
      if (mySession != session) return;
      setState(() {
        plan = Map<String, dynamic>.from(generated);
        selected = {for (final c in (plan!['test_cases'] as List)) if (c['selected'] == true) c['id'] as String};
        stage = Stage.approve;
      });
    } catch (e) {
      if (mySession != session) return;
      setState(() {
        error = _clean(e);
        stage = Stage.input;
      });
    }
  }

  Future<void> approveAndRun() async {
    final mySession = session;
    setState(() => busy = true);
    try {
      await api('POST', '/api/plans/${plan!['id']}/approve', {'approved': true, 'comment': 'Approved in NEXUS UI', 'selected_ids': selected.toList()});
      final started = await api('POST', '/api/runs', {'plan_id': plan!['id'], 'to_email': email.text.trim().isEmpty ? null : email.text.trim()});
      if (mySession != session) return;
      setState(() {
        run = Map<String, dynamic>.from(started);
        stage = Stage.execute;
        busy = false;
      });
      poller = Timer.periodic(const Duration(seconds: 1), (_) => pollRun(mySession));
    } catch (e) {
      if (mySession != session) return;
      setState(() {
        error = _clean(e);
        busy = false;
      });
    }
  }

  Future<void> pollRun(int mySession) async {
    if (run == null) return;
    try {
      final latest = Map<String, dynamic>.from(await api('GET', '/api/runs/${run!['id']}'));
      if (mySession != session) return;
      setState(() => run = latest);
      if (['passed', 'failed', 'error'].contains(latest['status'])) {
        poller?.cancel();
        await Future.delayed(const Duration(milliseconds: 600));
        if (mounted && mySession == session) setState(() => stage = Stage.report);
      }
    } catch (_) {
      // transient network error: keep polling
    }
  }

  Future<void> rejectPlan() async {
    try {
      await api('POST', '/api/plans/${plan!['id']}/approve', {'approved': false, 'comment': 'Rejected in NEXUS UI'});
    } catch (_) {}
    goHome();
  }

  void goHome() {
    poller?.cancel();
    setState(() {
      session++;
      stage = Stage.input;
      project = null;
      plan = null;
      run = null;
      error = null;
      busy = false;
    });
  }

  Future<void> pickExcel() async {
    final result = await FilePicker.platform.pickFiles(withData: true, type: FileType.custom, allowedExtensions: ['xlsx', 'csv']);
    if (result != null) setState(() => excelFile = result.files.single);
  }

  Future<void> downloadReport() => download('report', 'text/html', 'nexus-report', 'html');
  Future<void> downloadExcel() => download('excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'nexus-testcases', 'xlsx');

  Future<void> download(String endpoint, String type, String prefix, String extension) async {
    final id = run!['id'];
    try {
      final response = await http.get(Uri.parse('$apiBaseUrl/api/runs/$id/$endpoint'));
      if (response.statusCode >= 400) throw Exception('File not available');
      final blob = web.Blob([response.bodyBytes.toJS].toJS, web.BlobPropertyBag(type: type));
      final url = web.URL.createObjectURL(blob);
      final name = (run!['target']?['name'] ?? 'run').toString();
      final anchor = web.HTMLAnchorElement()
        ..href = url
        ..download = '$prefix-$name-${id.toString().substring(0, 8)}.$extension';
      web.document.body!.appendChild(anchor);
      anchor.click();
      anchor.remove();
      web.URL.revokeObjectURL(url);
    } catch (e) {
      _snack('Download failed: ${_clean(e)}');
    }
  }

  void openReport() => web.window.open(run!['report_url'], '_blank');

  Future<void> resendEmail() async {
    setState(() => busy = true);
    try {
      final result = await api('POST', '/api/runs/${run!['id']}/email', {'to_email': email.text.trim().isEmpty ? null : email.text.trim()});
      _snack(result['message']);
      setState(() => run!['email'] = {'sent': result['sent'], 'message': result['message']});
    } catch (e) {
      _snack(_clean(e));
    }
    if (mounted) setState(() => busy = false);
  }

  void _snack(String text) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  String _clean(Object e) => e.toString().replaceFirst('Exception: ', '');

  // ---------- Layout ----------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.white,
        surfaceTintColor: Colors.white,
        titleSpacing: 24,
        title: Row(children: [
          Container(
            width: 30, height: 30,
            decoration: BoxDecoration(color: brand, borderRadius: BorderRadius.circular(8)),
            child: const Icon(Icons.hub_outlined, color: Colors.white, size: 18),
          ),
          const SizedBox(width: 12),
          const Text('NEXUS', style: TextStyle(fontWeight: FontWeight.w800, letterSpacing: 1)),
          const SizedBox(width: 10),
          Text('Deploy · Test · Report', style: TextStyle(color: Colors.grey.shade600, fontSize: 14)),
        ]),
        actions: [
          if (stage != Stage.input)
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: TextButton.icon(onPressed: goHome, icon: const Icon(Icons.home_outlined), label: const Text('Back to home')),
            ),
        ],
        bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Container(height: 1, color: const Color(0xffe4e8f0))),
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1000),
          child: ListView(key: ValueKey(stage), padding: const EdgeInsets.fromLTRB(16, 24, 16, 48), children: [
            StageBar(current: stage),
            const SizedBox(height: 24),
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 250),
              child: SizedBox(key: ValueKey(stage), width: double.infinity, child: switch (stage) {
                Stage.input => inputView(),
                Stage.analyze => analyzeView(),
                Stage.approve => approveView(),
                Stage.execute => executeView(),
                Stage.report => reportView(),
              }),
            ),
          ]),
        ),
      ),
    );
  }

  Widget card({required Widget child, EdgeInsets padding = const EdgeInsets.all(24)}) => Container(
        margin: const EdgeInsets.only(bottom: 16),
        padding: padding,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: const Color(0xffe4e8f0)),
          boxShadow: const [BoxShadow(color: Color(0x0d172033), blurRadius: 16, offset: Offset(0, 4))],
        ),
        child: child,
      );

  Widget heading(String title, [String? subtitle]) => Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
          if (subtitle != null) Padding(padding: const EdgeInsets.only(top: 4), child: Text(subtitle, style: TextStyle(color: Colors.grey.shade600))),
        ]),
      );

  Widget errorBanner() => error == null
      ? const SizedBox.shrink()
      : Container(
          margin: const EdgeInsets.only(bottom: 16),
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(color: const Color(0xfffdecea), borderRadius: BorderRadius.circular(10)),
          child: Row(children: [
            const Icon(Icons.error_outline, color: badColor),
            const SizedBox(width: 10),
            Expanded(child: SelectableText(error!, style: const TextStyle(color: badColor))),
          ]),
        );

  // ---------- Stage 1: input ----------

  Widget inputView() {
    return card(
      padding: const EdgeInsets.all(32),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        heading('What should NEXUS test?', 'NEXUS deploys the repository as a container, runs the test cases you approve against it live, and emails the report.'),
        errorBanner(),
        TextField(
          controller: repoUrl,
          onChanged: (_) => setState(() {}),
          decoration: const InputDecoration(
            labelText: 'Git clone link',
            hintText: 'https://github.com/owner/repo.git',
            helperText: 'Public GitHub or GitLab repository with a Dockerfile at its root',
            prefixIcon: Icon(Icons.link),
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text('Sample repos:', style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          for (final entry in sampleRepos.entries)
            ActionChip(
              avatar: const Icon(Icons.inventory_2_outlined, size: 16),
              label: Text(entry.key),
              visualDensity: VisualDensity.compact,
              onPressed: () => setState(() => repoUrl.text = entry.value),
            ),
        ]),
        const SizedBox(height: 24),
        Text('What to test', style: Theme.of(context).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700)),
        const SizedBox(height: 10),
        SegmentedButton<Mode>(
          segments: const [
            ButtonSegment(value: Mode.discover, icon: Icon(Icons.travel_explore), label: Text('Discover automatically')),
            ButtonSegment(value: Mode.scenario, icon: Icon(Icons.notes), label: Text('Custom scenario')),
            ButtonSegment(value: Mode.excel, icon: Icon(Icons.table_chart_outlined), label: Text('Excel test cases')),
          ],
          selected: {mode},
          onSelectionChanged: (s) => setState(() => mode = s.first),
        ),
        const SizedBox(height: 14),
        switch (mode) {
          Mode.discover => modeHint(Icons.auto_awesome_outlined, 'NEXUS reads the README, Dockerfile, OpenAPI file and routes, and proposes test cases for the main features and error cases.'),
          Mode.scenario => TextField(
              controller: scenario,
              onChanged: (_) => setState(() {}),
              minLines: 4,
              maxLines: 8,
              decoration: const InputDecoration(
                labelText: 'Describe the scenario to test',
                hintText: 'e.g. A storage admin creates a 100 GB volume, grows it to 250 GB, confirms it cannot shrink, then deletes it.',
                helperText: 'NEXUS turns it into test cases against this repository',
                alignLabelWithHint: true,
                border: OutlineInputBorder(),
              ),
            ),
          Mode.excel => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                OutlinedButton.icon(onPressed: pickExcel, icon: const Icon(Icons.upload_file), label: const Text('Choose file')),
                const SizedBox(width: 14),
                Expanded(child: Text(excelFile?.name ?? 'No file chosen (.xlsx or .csv)', style: TextStyle(color: excelFile == null ? Colors.grey.shade600 : null))),
              ]),
              const SizedBox(height: 10),
              modeHint(Icons.playlist_add_check, 'Each row becomes a test case. Columns like ID, Test case, Method, Path, Body, Expected status are used directly; plain-English rows are interpreted by GPT. NEXUS also suggests cases your sheet is missing, and you choose which to add.'),
            ]),
        },
        const SizedBox(height: 18),
        TextField(
          controller: email,
          decoration: const InputDecoration(
            labelText: 'Email report to (optional)',
            hintText: 'Defaults to the configured recipient',
            prefixIcon: Icon(Icons.mail_outline),
            border: OutlineInputBorder(),
            isDense: true,
          ),
        ),
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: inputValid ? startAnalysis : null,
          icon: const Icon(Icons.arrow_forward),
          label: const Padding(padding: EdgeInsets.symmetric(vertical: 12, horizontal: 8), child: Text('Start')),
        ),
      ]),
    );
  }

  Widget modeHint(IconData icon, String text) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: const Color(0xfff2f5fc), borderRadius: BorderRadius.circular(10)),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, size: 18, color: brand),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: TextStyle(color: Colors.grey.shade800, fontSize: 13.5, height: 1.4))),
        ]),
      );

  // ---------- Stage 2: analyze ----------

  Widget analyzeView() {
    return card(
      padding: const EdgeInsets.symmetric(vertical: 56, horizontal: 24),
      child: Column(children: [
        const SizedBox(width: 44, height: 44, child: CircularProgressIndicator(strokeWidth: 3)),
        const SizedBox(height: 22),
        Text(analyzeStatus, style: Theme.of(context).textTheme.titleMedium, textAlign: TextAlign.center),
        const SizedBox(height: 6),
        Text('This usually takes 10–30 seconds.', style: TextStyle(color: Colors.grey.shade600)),
      ]),
    );
  }

  // ---------- Stage 3: approve ----------

  Widget approveView() {
    final p = project!;
    final cases = (plan!['test_cases'] as List).cast<Map>();
    final risks = (plan!['risks'] as List? ?? []).cast<Object>();
    final excelCases = cases.where((c) => c['source'] == 'excel').toList();
    final suggested = cases.where((c) => c['source'] == 'suggested').toList();
    final others = cases.where((c) => c['source'] != 'excel' && c['source'] != 'suggested').toList();
    final modeLabel = switch (p['mode']) {
      'scenario' => 'Custom scenario',
      'excel' => 'Excel test cases · ${p['excel_filename'] ?? ''}',
      _ => 'Discovered from the repository',
    };
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      card(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          heading(p['title'] ?? 'Project', p['repo_url']),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final s in (p['detected_stack'] as List)) Chip(label: Text('$s'), visualDensity: VisualDensity.compact),
          ]),
          const SizedBox(height: 12),
          for (final e in (p['evidence'] as List))
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(Icons.check, size: 16, color: Colors.grey.shade600),
                const SizedBox(width: 8),
                Expanded(child: Text('$e', style: TextStyle(color: Colors.grey.shade700))),
              ]),
            ),
        ]),
      ),
      card(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          heading('Execution plan', plan!['objective']),
          Row(children: [
            Icon(Icons.flag_outlined, size: 16, color: brand),
            const SizedBox(width: 6),
            Text(modeLabel, style: const TextStyle(fontWeight: FontWeight.w600, color: brand)),
          ]),
          if ((p['scenario'] ?? '').toString().isNotEmpty)
            Container(
              margin: const EdgeInsets.only(top: 10),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: const Color(0xfff2f5fc), borderRadius: BorderRadius.circular(10), border: const Border(left: BorderSide(color: brand, width: 3))),
              child: Text('${p['scenario']}', style: TextStyle(color: Colors.grey.shade800, fontStyle: FontStyle.italic)),
            ),
          if (risks.isNotEmpty) ...[
            const SizedBox(height: 14),
            const Text('Notes and risks', style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            for (final r in risks) Text('• $r', style: TextStyle(color: Colors.grey.shade700)),
          ],
          const SizedBox(height: 18),
          if (excelCases.isNotEmpty) caseGroup('From your spreadsheet', excelCases, null),
          if (suggested.isNotEmpty) caseGroup('Suggested additions', suggested, 'Not in your sheet. Tick the ones to run; they are added to the exported spreadsheet.'),
          if (others.isNotEmpty) caseGroup(p['mode'] == 'scenario' ? 'Scenario test cases' : 'Test cases', others, null),
        ]),
      ),
      errorBanner(),
      Wrap(alignment: WrapAlignment.end, crossAxisAlignment: WrapCrossAlignment.center, spacing: 12, runSpacing: 12, children: [
        Text('${selected.length} of ${cases.length} selected', style: TextStyle(color: Colors.grey.shade700)),
        OutlinedButton.icon(onPressed: busy ? null : rejectPlan, icon: const Icon(Icons.close), label: const Text('Reject')),
        FilledButton.icon(
          onPressed: busy || selected.isEmpty ? null : approveAndRun,
          icon: busy ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)) : const Icon(Icons.rocket_launch_outlined),
          label: Padding(
            padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 8),
            child: Text('Approve ${selected.length}, deploy & run'),
          ),
        ),
      ]),
    ]);
  }

  Widget caseGroup(String title, List<Map> cases, String? subtitle) {
    final ids = cases.map((c) => c['id'] as String).toSet();
    final chosen = ids.where(selected.contains).length;
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Checkbox(
            tristate: true,
            value: chosen == ids.length ? true : chosen == 0 ? false : null,
            onChanged: (_) => setState(() => chosen == ids.length ? selected.removeAll(ids) : selected.addAll(ids)),
          ),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('$title ($chosen/${ids.length})', style: const TextStyle(fontWeight: FontWeight.w700)),
              if (subtitle != null) Text(subtitle, style: TextStyle(color: Colors.grey.shade600, fontSize: 12.5)),
            ]),
          ),
        ]),
        for (final c in cases) testCaseRow(c),
      ]),
    );
  }

  Widget testCaseRow(Map c) {
    final id = c['id'] as String;
    final isOn = selected.contains(id);
    final suggestedCase = c['source'] == 'suggested';
    return InkWell(
      onTap: () => setState(() => isOn ? selected.remove(id) : selected.add(id)),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: const BoxDecoration(border: Border(top: BorderSide(color: Color(0xffeef1f6)))),
        child: Opacity(
          opacity: isOn ? 1 : .55,
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Checkbox(value: isOn, onChanged: (v) => setState(() => v == true ? selected.add(id) : selected.remove(id))),
            Padding(padding: const EdgeInsets.only(top: 12), child: MethodChip(method: '${c['method']}')),
            const SizedBox(width: 10),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    if (c['excel_ref'] != null) Text('${c['excel_ref']}  ', style: TextStyle(color: Colors.grey.shade500, fontWeight: FontWeight.w600)),
                    Expanded(child: Text('${c['name']}', style: const TextStyle(fontWeight: FontWeight.w600))),
                    if (suggestedCase)
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(color: brand.withValues(alpha: .1), borderRadius: BorderRadius.circular(99)),
                        child: const Text('SUGGESTED', style: TextStyle(color: brand, fontSize: 10.5, fontWeight: FontWeight.w700, letterSpacing: .5)),
                      ),
                  ]),
                  const SizedBox(height: 2),
                  Text('${c['path']}  →  expect HTTP ${c['expected_status']}${c['expected_contains'] != null ? ' containing “${c['expected_contains']}”' : ''}',
                      style: TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: Colors.grey.shade700)),
                  if (c['payload'] != null) Text('body ${jsonEncode(c['payload'])}', style: TextStyle(fontFamily: 'monospace', fontSize: 12, color: Colors.grey.shade600)),
                  if (suggestedCase && (c['description'] ?? '').toString().isNotEmpty)
                    Padding(padding: const EdgeInsets.only(top: 2), child: Text('${c['description']}', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600))),
                ]),
              ),
            ),
          ]),
        ),
      ),
    );
  }

  // ---------- Stage 4: execute ----------

  Widget executeView() {
    final r = run!;
    final stages = (r['stages'] as List).cast<Map>();
    final results = (r['results'] as List).cast<Map>();
    final total = (r['total_tests'] as num?)?.toInt() ?? 0;
    final log = (r['log'] as List).cast<Object>();
    final target = r['target'] as Map;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      card(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          heading('Deploying ${target['name']}', '${target['source']}'),
          for (final s in stages) stageRow(s),
        ]),
      ),
      card(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text('Live test results', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700))),
            Text('${results.length} / $total', style: TextStyle(color: Colors.grey.shade600)),
          ]),
          const SizedBox(height: 10),
          LinearProgressIndicator(value: total == 0 ? null : results.length / total, minHeight: 6, borderRadius: BorderRadius.circular(4)),
          const SizedBox(height: 8),
          if (results.isEmpty) Padding(padding: const EdgeInsets.symmetric(vertical: 12), child: Text('Waiting for the container to come up…', style: TextStyle(color: Colors.grey.shade600))),
          for (final res in results) resultRow(res),
          if (r['current_test'] != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Row(children: [
                const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
                const SizedBox(width: 12),
                Text('${r['current_test']}', style: TextStyle(color: Colors.grey.shade700)),
              ]),
            ),
        ]),
      ),
      card(
        padding: const EdgeInsets.all(0),
        child: Theme(
          data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            title: const Text('Live log', style: TextStyle(fontWeight: FontWeight.w600)),
            initiallyExpanded: true,
            childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            children: [
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: const Color(0xff101828), borderRadius: BorderRadius.circular(8)),
                child: SelectableText(
                  log.length > 14 ? log.sublist(log.length - 14).join('\n') : log.join('\n'),
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12, color: Color(0xffd0d5dd), height: 1.5),
                ),
              ),
            ],
          ),
        ),
      ),
    ]);
  }

  Widget stageRow(Map s) {
    final status = s['status'];
    final Widget icon = switch (status) {
      'running' => const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.5)),
      'done' => const Icon(Icons.check_circle, color: okColor, size: 22),
      'failed' => const Icon(Icons.cancel, color: badColor, size: 22),
      'skipped' => Icon(Icons.remove_circle_outline, color: Colors.grey.shade400, size: 22),
      _ => Icon(Icons.radio_button_unchecked, color: Colors.grey.shade400, size: 22),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 24, child: Center(child: icon)),
        const SizedBox(width: 14),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${s['label']}', style: TextStyle(fontWeight: status == 'running' ? FontWeight.w700 : FontWeight.w500, color: status == 'pending' ? Colors.grey.shade500 : null)),
            if ((s['detail'] ?? '').toString().isNotEmpty)
              Text('${s['detail']}', maxLines: 3, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12.5, color: status == 'failed' ? badColor : Colors.grey.shade600)),
          ]),
        ),
      ]),
    );
  }

  Widget resultRow(Map res) {
    final passed = res['status'] == 'passed';
    final skipped = res['status'] == 'skipped';
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 9),
      decoration: const BoxDecoration(border: Border(top: BorderSide(color: Color(0xffeef1f6)))),
      child: Row(children: [
        Icon(skipped ? Icons.remove_circle_outline : passed ? Icons.check_circle : Icons.cancel, color: skipped ? warnColor : passed ? okColor : badColor, size: 20),
        const SizedBox(width: 12),
        if (res['method'] != null) ...[MethodChip(method: '${res['method']}'), const SizedBox(width: 10)],
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${res['test_name']}', style: const TextStyle(fontWeight: FontWeight.w500)),
            Text(
              res['actual_status'] != null
                  ? '${res['path']}  ·  expected ${res['expected_status']}, got ${res['actual_status']}${res['duration_ms'] != null ? '  ·  ${res['duration_ms']} ms' : ''}'
                  : '${res['path'] ?? ''}  ${res['error'] ?? res['reason'] ?? ''}',
              style: TextStyle(fontFamily: 'monospace', fontSize: 12, color: Colors.grey.shade600),
            ),
          ]),
        ),
      ]),
    );
  }

  // ---------- Stage 5: report ----------

  Widget reportView() {
    final r = run!;
    final results = (r['results'] as List).cast<Map>();
    final analysis = (r['analysis'] as List).cast<Map>();
    final passed = results.where((x) => x['status'] == 'passed').length;
    final failed = results.where((x) => x['status'] == 'failed').length;
    final status = '${r['status']}';
    final colour = status == 'passed' ? okColor : badColor;
    final mail = (r['email'] as Map?) ?? {};
    final target = r['target'] as Map;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      card(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(status == 'passed' ? Icons.verified : status == 'failed' ? Icons.report_outlined : Icons.error_outline, color: colour, size: 40),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(status == 'passed' ? 'All tests passed' : status == 'failed' ? 'Some tests failed' : 'Run did not complete',
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700, color: colour)),
                Text('${target['name']}${(target['commit'] ?? '') != '' ? ' @ ${target['commit']}' : ''}', style: TextStyle(color: Colors.grey.shade600)),
              ]),
            ),
          ]),
          const SizedBox(height: 20),
          Wrap(spacing: 32, runSpacing: 12, children: [
            metric('Passed', '$passed', okColor),
            metric('Failed', '$failed', badColor),
            metric('Total', '${results.length}', null),
          ]),
          if (r['error'] != null) ...[
            const SizedBox(height: 16),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: const Color(0xfffdecea), borderRadius: BorderRadius.circular(8)),
              child: SelectableText('${r['error']}', style: const TextStyle(color: badColor, fontFamily: 'monospace', fontSize: 12.5)),
            ),
          ],
          const SizedBox(height: 18),
          Row(children: [
            Icon(mail['sent'] == true ? Icons.mark_email_read_outlined : Icons.mail_lock_outlined, size: 18, color: mail['sent'] == true ? okColor : warnColor),
            const SizedBox(width: 8),
            Expanded(child: Text('${mail['message'] ?? 'Email not sent'}', style: TextStyle(color: Colors.grey.shade700))),
          ]),
          const SizedBox(height: 6),
          Row(children: [
            const Icon(Icons.delete_sweep_outlined, size: 18, color: okColor),
            const SizedBox(width: 8),
            Expanded(child: Text('Target container and image removed. Only NEXUS is running.', style: TextStyle(color: Colors.grey.shade700))),
          ]),
          const SizedBox(height: 22),
          Wrap(spacing: 12, runSpacing: 12, children: [
            FilledButton.icon(onPressed: r['report_url'] == null ? null : downloadReport, icon: const Icon(Icons.download), label: const Text('Download report')),
            OutlinedButton.icon(onPressed: r['report_url'] == null ? null : downloadExcel, icon: const Icon(Icons.table_view_outlined), label: const Text('Download Excel')),
            OutlinedButton.icon(onPressed: r['report_url'] == null ? null : openReport, icon: const Icon(Icons.open_in_new), label: const Text('Open report')),
            OutlinedButton.icon(onPressed: busy ? null : resendEmail, icon: const Icon(Icons.forward_to_inbox), label: const Text('Email again')),
            TextButton.icon(onPressed: goHome, icon: const Icon(Icons.home_outlined), label: const Text('Back to home')),
          ]),
        ]),
      ),
      card(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Results', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 6),
          for (final res in results) resultRow(res),
        ]),
      ),
      if (analysis.isNotEmpty)
        card(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Failure analysis', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 10),
            for (final a in analysis)
              Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${a['test_name']}', style: const TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 4),
                  Text('Root cause: ${a['root_cause']}', style: TextStyle(color: Colors.grey.shade800)),
                  Text('Suggested action: ${a['suggested_action']}', style: TextStyle(color: Colors.grey.shade700)),
                ]),
              ),
          ]),
        ),
    ]);
  }

  Widget metric(String label, String value, Color? colour) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label.toUpperCase(), style: TextStyle(fontSize: 11, letterSpacing: 1, color: Colors.grey.shade600)),
        Text(value, style: TextStyle(fontSize: 30, fontWeight: FontWeight.w800, color: colour)),
      ]);
}

class StageBar extends StatelessWidget {
  final Stage current;
  const StageBar({super.key, required this.current});

  @override
  Widget build(BuildContext context) {
    final items = <Widget>[];
    for (final s in Stage.values) {
      final done = s.index < current.index;
      final active = s == current;
      if (s.index > 0) {
        items.add(Expanded(child: Container(height: 2, margin: const EdgeInsets.only(bottom: 22), color: done || active ? brand : const Color(0xffdde3ec))));
      }
      items.add(Column(mainAxisSize: MainAxisSize.min, children: [
        AnimatedContainer(
          duration: const Duration(milliseconds: 250),
          width: 30, height: 30,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: done ? brand : active ? brand.withValues(alpha: .12) : Colors.white,
            border: Border.all(color: done || active ? brand : const Color(0xffcfd6e2), width: 2),
          ),
          child: Center(
            child: done
                ? const Icon(Icons.check, size: 16, color: Colors.white)
                : Text('${s.index + 1}', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13, color: active ? brand : Colors.grey.shade500)),
          ),
        ),
        const SizedBox(height: 6),
        Text(stageLabels[s]!, style: TextStyle(fontSize: 12, fontWeight: active ? FontWeight.w700 : FontWeight.w500, color: active ? brand : done ? Colors.black87 : Colors.grey.shade500)),
      ]));
    }
    return Row(crossAxisAlignment: CrossAxisAlignment.center, children: items);
  }
}

class MethodChip extends StatelessWidget {
  final String method;
  const MethodChip({super.key, required this.method});

  @override
  Widget build(BuildContext context) {
    final colour = switch (method.toUpperCase()) {
      'GET' => const Color(0xff2457d6),
      'POST' => okColor,
      'PUT' || 'PATCH' => warnColor,
      'DELETE' => badColor,
      _ => Colors.grey,
    };
    return Container(
      width: 62,
      padding: const EdgeInsets.symmetric(vertical: 3),
      decoration: BoxDecoration(color: colour.withValues(alpha: .1), borderRadius: BorderRadius.circular(6)),
      child: Text(method.toUpperCase(), textAlign: TextAlign.center, style: TextStyle(color: colour, fontWeight: FontWeight.w700, fontSize: 11.5, fontFamily: 'monospace')),
    );
  }
}
