"""Mobile framework configurations for all 5 supported platforms.

Each config is registered at module load time via ``register_mobile()``.
Frameworks cover: cross-platform (React Native, Flutter, Expo) and native
(Swift/SwiftUI for iOS, Kotlin/Compose for Android).
"""

from app.agents.mobile_configs import MobileConfig, register_mobile

# ---------------------------------------------------------------------------
# 1. React Native -- cross-platform (TypeScript)
# ---------------------------------------------------------------------------

REACT_NATIVE = MobileConfig(
    name="react_native",
    display_name="React Native",
    language="typescript",
    code_block_lang="tsx",
    platform="cross_platform",
    component_extension=".tsx",
    file_structure={
        "screen": "src/screens/{Name}Screen.tsx",
        "navigation": "src/navigation/AppNavigator.tsx",
        "store": "src/stores/{name}Store.ts",
        "component": "src/components/{Name}.tsx",
        "service": "src/services/{name}Service.ts",
    },
    rules=(
        "1. Use functional components exclusively; never use class components. "
        "Prefer arrow-function syntax with explicit return types for props.",

        "2. Manage local state with useState and useReducer; lift shared state "
        "into Zustand stores or React Context. Never mutate state directly.",

        "3. Use React Navigation v6+ for all routing. Define screen params with "
        "TypeScript generics on NativeStackScreenProps and validate navigation "
        "props at compile time.",

        "4. Use AsyncStorage only for small key-value data (tokens, preferences). "
        "For structured data, use WatermelonDB or MMKV with typed schemas.",

        "5. Wrap all screens in SafeAreaView from react-native-safe-area-context "
        "to avoid notch and status-bar overlap on iOS and Android.",

        "6. Use FlatList or SectionList for all scrollable lists; never use "
        "ScrollView with .map() for dynamic data. Provide keyExtractor and "
        "getItemLayout for optimal virtualization performance.",

        "7. Define all styles with StyleSheet.create() at module scope; never "
        "use inline style objects to avoid unnecessary re-renders.",

        "8. Use Platform.select() or Platform.OS checks for platform-specific "
        "behavior. Prefer platform-specific file extensions (.ios.tsx, .android.tsx) "
        "for divergent implementations exceeding 20 lines.",

        "9. Wrap screen-level components in ErrorBoundary to catch rendering "
        "errors gracefully; display a fallback UI with retry capability.",

        "10. Use Expo modules (expo-camera, expo-location, expo-file-system) "
        "when available instead of bare React Native alternatives for better "
        "cross-platform consistency and OTA update support.",

        "11. NEVER invent or guess import paths. Only import from packages "
        "listed in package.json and from files that exist in the project. "
        "Verify every third-party import against the installed dependencies.",

        "12. Output ONLY valid TypeScript/TSX code. Do not include prose, "
        "explanations, or commentary inside code blocks. Every code block "
        "must be syntactically correct and ready to save to a file.",
    ),
    golden_examples={
        "screen": (
            "import React, { useCallback, useEffect, useState } from 'react';\n"
            "import { FlatList, StyleSheet, Text, View } from 'react-native';\n"
            "import { SafeAreaView } from 'react-native-safe-area-context';\n"
            "import type { NativeStackScreenProps } from '@react-navigation/native-stack';\n"
            "import type { RootStackParamList } from '../navigation/AppNavigator';\n"
            "import { useTaskStore } from '../stores/taskStore';\n"
            "\n"
            "type Props = NativeStackScreenProps<RootStackParamList, 'TaskList'>;\n"
            "\n"
            "export default function TaskListScreen({ navigation }: Props) {\n"
            "  const { tasks, fetchTasks, loading } = useTaskStore();\n"
            "\n"
            "  useEffect(() => {\n"
            "    fetchTasks();\n"
            "  }, [fetchTasks]);\n"
            "\n"
            "  const renderItem = useCallback(\n"
            "    ({ item }: { item: Task }) => (\n"
            "      <View style={styles.card}>\n"
            "        <Text style={styles.title}>{item.title}</Text>\n"
            "        <Text style={styles.subtitle}>{item.status}</Text>\n"
            "      </View>\n"
            "    ),\n"
            "    [],\n"
            "  );\n"
            "\n"
            "  return (\n"
            "    <SafeAreaView style={styles.container}>\n"
            "      <FlatList\n"
            "        data={tasks}\n"
            "        renderItem={renderItem}\n"
            "        keyExtractor={(item) => item.id}\n"
            "        refreshing={loading}\n"
            "        onRefresh={fetchTasks}\n"
            "      />\n"
            "    </SafeAreaView>\n"
            "  );\n"
            "}\n"
            "\n"
            "const styles = StyleSheet.create({\n"
            "  container: { flex: 1, backgroundColor: '#fff' },\n"
            "  card: { padding: 16, borderBottomWidth: 1, borderBottomColor: '#eee' },\n"
            "  title: { fontSize: 16, fontWeight: '600' },\n"
            "  subtitle: { fontSize: 12, color: '#666', marginTop: 4 },\n"
            "});"
        ),
        "navigation": (
            "import React from 'react';\n"
            "import { NavigationContainer } from '@react-navigation/native';\n"
            "import { createNativeStackNavigator } from '@react-navigation/native-stack';\n"
            "import TaskListScreen from '../screens/TaskListScreen';\n"
            "import TaskDetailScreen from '../screens/TaskDetailScreen';\n"
            "\n"
            "export type RootStackParamList = {\n"
            "  TaskList: undefined;\n"
            "  TaskDetail: { taskId: string };\n"
            "};\n"
            "\n"
            "const Stack = createNativeStackNavigator<RootStackParamList>();\n"
            "\n"
            "export default function AppNavigator() {\n"
            "  return (\n"
            "    <NavigationContainer>\n"
            "      <Stack.Navigator initialRouteName=\"TaskList\">\n"
            "        <Stack.Screen\n"
            "          name=\"TaskList\"\n"
            "          component={TaskListScreen}\n"
            "          options={{ title: 'Tasks' }}\n"
            "        />\n"
            "        <Stack.Screen\n"
            "          name=\"TaskDetail\"\n"
            "          component={TaskDetailScreen}\n"
            "          options={{ title: 'Task Detail' }}\n"
            "        />\n"
            "      </Stack.Navigator>\n"
            "    </NavigationContainer>\n"
            "  );\n"
            "}"
        ),
        "store": (
            "import { create } from 'zustand';\n"
            "import { taskService } from '../services/taskService';\n"
            "\n"
            "export interface Task {\n"
            "  id: string;\n"
            "  title: string;\n"
            "  status: 'pending' | 'done';\n"
            "}\n"
            "\n"
            "interface TaskState {\n"
            "  tasks: Task[];\n"
            "  loading: boolean;\n"
            "  error: string | null;\n"
            "  fetchTasks: () => Promise<void>;\n"
            "  toggleTask: (id: string) => void;\n"
            "}\n"
            "\n"
            "export const useTaskStore = create<TaskState>((set, get) => ({\n"
            "  tasks: [],\n"
            "  loading: false,\n"
            "  error: null,\n"
            "  fetchTasks: async () => {\n"
            "    set({ loading: true, error: null });\n"
            "    try {\n"
            "      const tasks = await taskService.getAll();\n"
            "      set({ tasks, loading: false });\n"
            "    } catch (err) {\n"
            "      set({ error: (err as Error).message, loading: false });\n"
            "    }\n"
            "  },\n"
            "  toggleTask: (id: string) => {\n"
            "    const tasks = get().tasks.map((t) =>\n"
            "      t.id === id ? { ...t, status: t.status === 'done' ? 'pending' : 'done' } : t,\n"
            "    );\n"
            "    set({ tasks } as Partial<TaskState>);\n"
            "  },\n"
            "}));"
        ),
        "component": (
            "import React from 'react';\n"
            "import { Pressable, StyleSheet, Text, View } from 'react-native';\n"
            "\n"
            "interface TaskCardProps {\n"
            "  title: string;\n"
            "  status: 'pending' | 'done';\n"
            "  onPress: () => void;\n"
            "}\n"
            "\n"
            "export default function TaskCard({ title, status, onPress }: TaskCardProps) {\n"
            "  return (\n"
            "    <Pressable\n"
            "      style={({ pressed }) => [\n"
            "        styles.card,\n"
            "        pressed && styles.pressed,\n"
            "      ]}\n"
            "      onPress={onPress}\n"
            "    >\n"
            "      <View style={styles.row}>\n"
            "        <View style={[styles.dot, status === 'done' && styles.dotDone]} />\n"
            "        <Text style={[styles.title, status === 'done' && styles.titleDone]}>\n"
            "          {title}\n"
            "        </Text>\n"
            "      </View>\n"
            "    </Pressable>\n"
            "  );\n"
            "}\n"
            "\n"
            "const styles = StyleSheet.create({\n"
            "  card: { padding: 16, backgroundColor: '#fff', borderRadius: 8, marginBottom: 8 },\n"
            "  pressed: { opacity: 0.7 },\n"
            "  row: { flexDirection: 'row', alignItems: 'center' },\n"
            "  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: '#ccc', marginRight: 12 },\n"
            "  dotDone: { backgroundColor: '#34C759' },\n"
            "  title: { fontSize: 16, color: '#1a1a1a' },\n"
            "  titleDone: { textDecorationLine: 'line-through', color: '#999' },\n"
            "});"
        ),
    },
    supports_hot_reload=True,
    min_sdk_version="0.72",
    package_manager="npm",
)

# ---------------------------------------------------------------------------
# 2. Flutter -- cross-platform (Dart)
# ---------------------------------------------------------------------------

FLUTTER = MobileConfig(
    name="flutter",
    display_name="Flutter",
    language="dart",
    code_block_lang="dart",
    platform="cross_platform",
    component_extension=".dart",
    file_structure={
        "screen": "lib/screens/{name}_screen.dart",
        "model": "lib/models/{name}.dart",
        "provider": "lib/providers/{name}_provider.dart",
        "widget": "lib/widgets/{name}_widget.dart",
        "service": "lib/services/{name}_service.dart",
    },
    rules=(
        "1. Prefer StatelessWidget for UI that depends only on its constructor "
        "arguments. Use StatefulWidget only when the widget owns local mutable "
        "state (animations, text controllers, focus nodes).",

        "2. Use Riverpod (riverpod 2.x) for dependency injection and state "
        "management. Declare providers at the top level as final globals and "
        "access them via ref.watch() inside build methods.",

        "3. Use go_router for declarative routing with typed path parameters. "
        "Define all routes in a single GoRouter instance and use context.go() "
        "or context.push() for navigation.",

        "4. Define data models with Freezed and json_serializable for "
        "immutable value types with copyWith, equality, and JSON serialization. "
        "Run build_runner after modifying any @freezed class.",

        "5. Use Dio for HTTP requests with interceptors for auth tokens, "
        "logging, and retry logic. Create a single Dio instance per base URL "
        "and share it via a Riverpod Provider.",

        "6. Never store BuildContext in variables that outlive the current "
        "synchronous frame. Always check mounted before using context after "
        "an await in StatefulWidget methods.",

        "7. Assign GlobalKey or ValueKey to widgets in lists and animated "
        "transitions to preserve state correctly. Use UniqueKey only when "
        "you explicitly want to force widget recreation.",

        "8. Use Form with GlobalKey<FormState> and TextFormField validators "
        "for all user input. Call formKey.currentState!.validate() before "
        "processing form submissions.",

        "9. Structure the widget tree with const constructors wherever "
        "possible. Mark constructor and static widgets as const to enable "
        "compile-time constant folding and reduce rebuilds.",

        "10. Use Theme.of(context) and MediaQuery.of(context) for responsive "
        "styling. Define custom ThemeExtension classes for app-specific "
        "design tokens (colors, spacing, typography).",

        "11. NEVER invent or guess import paths. Only import from packages "
        "listed in pubspec.yaml and from files that exist in the lib/ "
        "directory. Verify every third-party import before using it.",

        "12. Output ONLY valid Dart code. Do not include prose, explanations, "
        "or commentary inside code blocks. Every code block must be "
        "syntactically correct and ready to save to a .dart file.",
    ),
    golden_examples={
        "screen": (
            "import 'package:flutter/material.dart';\n"
            "import 'package:flutter_riverpod/flutter_riverpod.dart';\n"
            "import '../providers/task_provider.dart';\n"
            "import '../widgets/task_card_widget.dart';\n"
            "\n"
            "class TaskListScreen extends ConsumerWidget {\n"
            "  const TaskListScreen({super.key});\n"
            "\n"
            "  @override\n"
            "  Widget build(BuildContext context, WidgetRef ref) {\n"
            "    final tasksAsync = ref.watch(taskListProvider);\n"
            "\n"
            "    return Scaffold(\n"
            "      appBar: AppBar(title: const Text('Tasks')),\n"
            "      body: tasksAsync.when(\n"
            "        data: (tasks) => ListView.builder(\n"
            "          itemCount: tasks.length,\n"
            "          itemBuilder: (context, index) => TaskCardWidget(\n"
            "            task: tasks[index],\n"
            "            onTap: () => context.push('/tasks/${tasks[index].id}'),\n"
            "          ),\n"
            "        ),\n"
            "        loading: () => const Center(child: CircularProgressIndicator()),\n"
            "        error: (err, stack) => Center(child: Text('Error: $err')),\n"
            "      ),\n"
            "      floatingActionButton: FloatingActionButton(\n"
            "        onPressed: () => context.push('/tasks/new'),\n"
            "        child: const Icon(Icons.add),\n"
            "      ),\n"
            "    );\n"
            "  }\n"
            "}"
        ),
        "model": (
            "import 'package:freezed_annotation/freezed_annotation.dart';\n"
            "import 'package:json_annotation/json_annotation.dart';\n"
            "\n"
            "part 'task.freezed.dart';\n"
            "part 'task.g.dart';\n"
            "\n"
            "@freezed\n"
            "class Task with _$Task {\n"
            "  const factory Task({\n"
            "    required String id,\n"
            "    required String title,\n"
            "    @Default('') String description,\n"
            "    @Default(false) bool completed,\n"
            "    required DateTime createdAt,\n"
            "  }) = _Task;\n"
            "\n"
            "  factory Task.fromJson(Map<String, dynamic> json) =>\n"
            "      _$TaskFromJson(json);\n"
            "}\n"
            "\n"
            "enum TaskFilter { all, active, completed }\n"
            "\n"
            "extension TaskFilterX on TaskFilter {\n"
            "  List<Task> apply(List<Task> tasks) {\n"
            "    return switch (this) {\n"
            "      TaskFilter.all => tasks,\n"
            "      TaskFilter.active => tasks.where((t) => !t.completed).toList(),\n"
            "      TaskFilter.completed => tasks.where((t) => t.completed).toList(),\n"
            "    };\n"
            "  }\n"
            "}"
        ),
        "provider": (
            "import 'package:flutter_riverpod/flutter_riverpod.dart';\n"
            "import '../models/task.dart';\n"
            "import '../services/task_service.dart';\n"
            "\n"
            "final taskServiceProvider = Provider<TaskService>((ref) {\n"
            "  return TaskService(ref);\n"
            "});\n"
            "\n"
            "final taskListProvider = FutureProvider<List<Task>>((ref) async {\n"
            "  final service = ref.watch(taskServiceProvider);\n"
            "  return service.fetchAll();\n"
            "});\n"
            "\n"
            "final taskFilterProvider = StateProvider<TaskFilter>((ref) {\n"
            "  return TaskFilter.all;\n"
            "});\n"
            "\n"
            "final filteredTasksProvider = Provider<AsyncValue<List<Task>>>((ref) {\n"
            "  final filter = ref.watch(taskFilterProvider);\n"
            "  final tasksAsync = ref.watch(taskListProvider);\n"
            "  return tasksAsync.whenData((tasks) => filter.apply(tasks));\n"
            "});"
        ),
        "widget": (
            "import 'package:flutter/material.dart';\n"
            "import '../models/task.dart';\n"
            "\n"
            "class TaskCardWidget extends StatelessWidget {\n"
            "  const TaskCardWidget({\n"
            "    super.key,\n"
            "    required this.task,\n"
            "    required this.onTap,\n"
            "  });\n"
            "\n"
            "  final Task task;\n"
            "  final VoidCallback onTap;\n"
            "\n"
            "  @override\n"
            "  Widget build(BuildContext context) {\n"
            "    final theme = Theme.of(context);\n"
            "    return Card(\n"
            "      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),\n"
            "      child: ListTile(\n"
            "        leading: Icon(\n"
            "          task.completed ? Icons.check_circle : Icons.radio_button_unchecked,\n"
            "          color: task.completed ? Colors.green : Colors.grey,\n"
            "        ),\n"
            "        title: Text(\n"
            "          task.title,\n"
            "          style: theme.textTheme.bodyLarge?.copyWith(\n"
            "            decoration: task.completed ? TextDecoration.lineThrough : null,\n"
            "          ),\n"
            "        ),\n"
            "        subtitle: Text(task.description, maxLines: 1, overflow: TextOverflow.ellipsis),\n"
            "        onTap: onTap,\n"
            "      ),\n"
            "    );\n"
            "  }\n"
            "}"
        ),
    },
    supports_hot_reload=True,
    min_sdk_version="3.22",
    package_manager="pub",
)

# ---------------------------------------------------------------------------
# 3. Swift / SwiftUI -- iOS native
# ---------------------------------------------------------------------------

SWIFT = MobileConfig(
    name="swift",
    display_name="Swift / SwiftUI",
    language="swift",
    code_block_lang="swift",
    platform="ios",
    component_extension=".swift",
    file_structure={
        "view": "Views/{Name}View.swift",
        "viewmodel": "ViewModels/{Name}ViewModel.swift",
        "model": "Models/{Name}.swift",
        "service": "Services/{Name}Service.swift",
    },
    rules=(
        "1. Follow the MVVM pattern strictly: Views observe ViewModels, ViewModels "
        "call Services, Services handle networking and persistence. Views never "
        "call Services directly.",

        "2. Use @State for view-local value types, @Binding for child-to-parent "
        "communication, @StateObject for owned reference-type state, and "
        "@ObservedObject for injected reference-type state. Never use @StateObject "
        "for objects created outside the view.",

        "3. Use NavigationStack (iOS 16+) with NavigationPath for programmatic "
        "navigation. Define navigation destinations with .navigationDestination(for:) "
        "using Hashable model types.",

        "4. Use Swift concurrency (async/await, Task, TaskGroup) for all "
        "asynchronous work. Never use completion handlers or DispatchQueue "
        "unless interfacing with legacy callback-based APIs.",

        "5. Use Combine only for reactive streams that need operators (debounce, "
        "combineLatest, throttle). For simple async operations, prefer async/await. "
        "Always store cancellables in a Set<AnyCancellable> on the ViewModel.",

        "6. Use SwiftData or CoreData for local persistence. Define models with "
        "@Model macro (SwiftData) or NSManagedObject subclasses (CoreData). "
        "Perform writes on a background context to keep the main thread free.",

        "7. Use URLSession with async/await for networking. Create a centralized "
        "APIClient actor that handles base URL, auth headers, JSON decoding, "
        "and error mapping in one place.",

        "8. Provide SwiftUI previews for every view using #Preview macro. "
        "Supply mock data and view models so previews render without network "
        "calls or database access.",

        "9. Annotate all ViewModel classes and any code that updates @Published "
        "properties with @MainActor to guarantee UI updates happen on the main "
        "thread. Use nonisolated for background-safe helper methods.",

        "10. Use the Observation framework (@Observable macro) on iOS 17+ "
        "instead of ObservableObject/Published for simpler reactive state. "
        "Fall back to ObservableObject only when targeting iOS 16.",

        "11. NEVER invent or guess import paths. Only import system frameworks "
        "(Foundation, SwiftUI, Combine) and packages declared in Package.swift "
        "or the Xcode project. Verify every third-party import exists.",

        "12. Output ONLY valid Swift code. Do not include prose, explanations, "
        "or commentary inside code blocks. Every code block must compile "
        "under strict concurrency checking with Swift 5.10+.",
    ),
    golden_examples={
        "view": (
            "import SwiftUI\n"
            "\n"
            "struct TaskListView: View {\n"
            "    @StateObject private var viewModel = TaskListViewModel()\n"
            "    @State private var showingAddSheet = false\n"
            "\n"
            "    var body: some View {\n"
            "        NavigationStack {\n"
            "            Group {\n"
            "                if viewModel.isLoading {\n"
            "                    ProgressView(\"Loading tasks...\")\n"
            "                } else {\n"
            "                    List(viewModel.tasks) { task in\n"
            "                        NavigationLink(value: task) {\n"
            "                            TaskRowView(task: task)\n"
            "                        }\n"
            "                    }\n"
            "                    .refreshable { await viewModel.fetchTasks() }\n"
            "                }\n"
            "            }\n"
            "            .navigationTitle(\"Tasks\")\n"
            "            .navigationDestination(for: TaskModel.self) { task in\n"
            "                TaskDetailView(task: task)\n"
            "            }\n"
            "            .toolbar {\n"
            "                Button { showingAddSheet = true } label: {\n"
            "                    Image(systemName: \"plus\")\n"
            "                }\n"
            "            }\n"
            "            .sheet(isPresented: $showingAddSheet) {\n"
            "                AddTaskView(viewModel: viewModel)\n"
            "            }\n"
            "        }\n"
            "        .task { await viewModel.fetchTasks() }\n"
            "    }\n"
            "}\n"
            "\n"
            "#Preview {\n"
            "    TaskListView()\n"
            "}"
        ),
        "viewmodel": (
            "import Foundation\n"
            "import SwiftUI\n"
            "\n"
            "@MainActor\n"
            "final class TaskListViewModel: ObservableObject {\n"
            "    @Published private(set) var tasks: [TaskModel] = []\n"
            "    @Published private(set) var isLoading = false\n"
            "    @Published var errorMessage: String?\n"
            "\n"
            "    private let service: TaskServiceProtocol\n"
            "\n"
            "    init(service: TaskServiceProtocol = TaskService()) {\n"
            "        self.service = service\n"
            "    }\n"
            "\n"
            "    func fetchTasks() async {\n"
            "        isLoading = true\n"
            "        defer { isLoading = false }\n"
            "        do {\n"
            "            tasks = try await service.fetchAll()\n"
            "        } catch {\n"
            "            errorMessage = error.localizedDescription\n"
            "        }\n"
            "    }\n"
            "\n"
            "    func toggleComplete(_ task: TaskModel) async {\n"
            "        guard let index = tasks.firstIndex(where: { $0.id == task.id }) else { return }\n"
            "        var updated = tasks[index]\n"
            "        updated.isCompleted.toggle()\n"
            "        do {\n"
            "            tasks[index] = try await service.update(updated)\n"
            "        } catch {\n"
            "            errorMessage = error.localizedDescription\n"
            "        }\n"
            "    }\n"
            "}"
        ),
        "model": (
            "import Foundation\n"
            "\n"
            "struct TaskModel: Identifiable, Hashable, Codable {\n"
            "    let id: UUID\n"
            "    var title: String\n"
            "    var description: String\n"
            "    var isCompleted: Bool\n"
            "    let createdAt: Date\n"
            "\n"
            "    init(\n"
            "        id: UUID = UUID(),\n"
            "        title: String,\n"
            "        description: String = \"\",\n"
            "        isCompleted: Bool = false,\n"
            "        createdAt: Date = Date()\n"
            "    ) {\n"
            "        self.id = id\n"
            "        self.title = title\n"
            "        self.description = description\n"
            "        self.isCompleted = isCompleted\n"
            "        self.createdAt = createdAt\n"
            "    }\n"
            "}\n"
            "\n"
            "extension TaskModel {\n"
            "    static let preview = TaskModel(\n"
            "        title: \"Buy groceries\",\n"
            "        description: \"Milk, eggs, bread\"\n"
            "    )\n"
            "\n"
            "    static let previewList: [TaskModel] = [\n"
            "        TaskModel(title: \"Buy groceries\", description: \"Milk, eggs, bread\"),\n"
            "        TaskModel(title: \"Walk the dog\", isCompleted: true),\n"
            "        TaskModel(title: \"Read SwiftUI docs\", description: \"Chapter 5\"),\n"
            "    ]\n"
            "}"
        ),
        "service": (
            "import Foundation\n"
            "\n"
            "protocol TaskServiceProtocol {\n"
            "    func fetchAll() async throws -> [TaskModel]\n"
            "    func update(_ task: TaskModel) async throws -> TaskModel\n"
            "    func delete(id: UUID) async throws\n"
            "}\n"
            "\n"
            "actor TaskService: TaskServiceProtocol {\n"
            "    private let baseURL = URL(string: \"https://api.example.com/v1\")!\n"
            "    private let session: URLSession\n"
            "    private let decoder: JSONDecoder\n"
            "\n"
            "    init(session: URLSession = .shared) {\n"
            "        self.session = session\n"
            "        self.decoder = JSONDecoder()\n"
            "        self.decoder.dateDecodingStrategy = .iso8601\n"
            "    }\n"
            "\n"
            "    func fetchAll() async throws -> [TaskModel] {\n"
            "        let url = baseURL.appending(path: \"tasks\")\n"
            "        let (data, response) = try await session.data(from: url)\n"
            "        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {\n"
            "            throw URLError(.badServerResponse)\n"
            "        }\n"
            "        return try decoder.decode([TaskModel].self, from: data)\n"
            "    }\n"
            "\n"
            "    func update(_ task: TaskModel) async throws -> TaskModel {\n"
            "        var request = URLRequest(url: baseURL.appending(path: \"tasks/\\(task.id)\"))\n"
            "        request.httpMethod = \"PUT\"\n"
            "        request.setValue(\"application/json\", forHTTPHeaderField: \"Content-Type\")\n"
            "        request.httpBody = try JSONEncoder().encode(task)\n"
            "        let (data, _) = try await session.data(for: request)\n"
            "        return try decoder.decode(TaskModel.self, from: data)\n"
            "    }\n"
            "\n"
            "    func delete(id: UUID) async throws {\n"
            "        var request = URLRequest(url: baseURL.appending(path: \"tasks/\\(id)\"))\n"
            "        request.httpMethod = \"DELETE\"\n"
            "        let (_, response) = try await session.data(for: request)\n"
            "        guard let http = response as? HTTPURLResponse, http.statusCode == 204 else {\n"
            "            throw URLError(.badServerResponse)\n"
            "        }\n"
            "    }\n"
            "}"
        ),
    },
    supports_hot_reload=False,
    min_sdk_version="16.0",
    package_manager="cocoapods",
)

# ---------------------------------------------------------------------------
# 4. Kotlin / Jetpack Compose -- Android native
# ---------------------------------------------------------------------------

KOTLIN_COMPOSE = MobileConfig(
    name="kotlin_compose",
    display_name="Kotlin / Jetpack Compose",
    language="kotlin",
    code_block_lang="kotlin",
    platform="android",
    component_extension=".kt",
    file_structure={
        "screen": "ui/screens/{Name}Screen.kt",
        "viewmodel": "viewmodel/{Name}ViewModel.kt",
        "model": "model/{Name}.kt",
        "repository": "data/repository/{Name}Repository.kt",
    },
    rules=(
        "1. Build all UI with Jetpack Compose; never use XML layouts or "
        "View-based widgets. Use Material 3 composables and theme tokens "
        "for consistent styling.",

        "2. Use Android ViewModel from lifecycle-viewmodel-compose for "
        "screen-level state. Expose UI state as StateFlow and collect it "
        "in composables with collectAsStateWithLifecycle().",

        "3. Use Hilt for dependency injection. Annotate the Application "
        "class with @HiltAndroidApp, ViewModels with @HiltViewModel and "
        "@Inject constructor, and entry points with @AndroidEntryPoint.",

        "4. Use Room for local persistence. Define entities with @Entity, "
        "DAOs with @Dao and suspend functions, and the database with "
        "@Database. Use Flow return types for reactive queries.",

        "5. Use Retrofit with kotlinx.serialization for networking. Define "
        "API interfaces with suspend functions and @Serializable data "
        "classes. Provide Retrofit instances via Hilt modules.",

        "6. Use LazyColumn and LazyRow for all scrollable lists. Provide "
        "a stable key parameter to each item {} block to enable correct "
        "recomposition and animation of list items.",

        "7. Use Navigation Compose with a sealed class or enum for route "
        "definitions. Pass arguments via typed route parameters and use "
        "NavBackStackEntry to retrieve them in destination composables.",

        "8. Use remember {} for computation caching within a composition "
        "and rememberSaveable {} for state that must survive configuration "
        "changes (rotation, process death). Never store heavy objects in "
        "rememberSaveable.",

        "9. Launch coroutines in ViewModel using viewModelScope. Use "
        "Dispatchers.IO for network and database calls. Never use "
        "GlobalScope or runBlocking in production code.",

        "10. Handle side effects with LaunchedEffect for suspend functions "
        "and DisposableEffect for cleanup. Match effect keys to the values "
        "that should trigger re-execution.",

        "11. NEVER invent or guess import paths. Only import from libraries "
        "declared in build.gradle.kts and from project modules that exist. "
        "Verify every third-party import against declared dependencies.",

        "12. Output ONLY valid Kotlin code. Do not include prose, "
        "explanations, or commentary inside code blocks. Every code block "
        "must compile against the declared minSdk and dependency versions.",
    ),
    golden_examples={
        "screen": (
            "package com.example.app.ui.screens\n"
            "\n"
            "import androidx.compose.foundation.layout.*\n"
            "import androidx.compose.foundation.lazy.LazyColumn\n"
            "import androidx.compose.foundation.lazy.items\n"
            "import androidx.compose.material3.*\n"
            "import androidx.compose.runtime.Composable\n"
            "import androidx.compose.runtime.getValue\n"
            "import androidx.compose.ui.Alignment\n"
            "import androidx.compose.ui.Modifier\n"
            "import androidx.compose.ui.unit.dp\n"
            "import androidx.hilt.navigation.compose.hiltViewModel\n"
            "import androidx.lifecycle.compose.collectAsStateWithLifecycle\n"
            "import com.example.app.model.Task\n"
            "import com.example.app.viewmodel.TaskListViewModel\n"
            "\n"
            "@Composable\n"
            "fun TaskListScreen(\n"
            "    onTaskClick: (String) -> Unit,\n"
            "    viewModel: TaskListViewModel = hiltViewModel(),\n"
            ") {\n"
            "    val uiState by viewModel.uiState.collectAsStateWithLifecycle()\n"
            "\n"
            "    Scaffold(\n"
            "        floatingActionButton = {\n"
            "            FloatingActionButton(onClick = { onTaskClick(\"new\") }) {\n"
            "                Icon(Icons.Default.Add, contentDescription = \"Add task\")\n"
            "            }\n"
            "        },\n"
            "    ) { padding ->\n"
            "        when {\n"
            "            uiState.isLoading -> {\n"
            "                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {\n"
            "                    CircularProgressIndicator()\n"
            "                }\n"
            "            }\n"
            "            else -> {\n"
            "                LazyColumn(contentPadding = padding) {\n"
            "                    items(uiState.tasks, key = { it.id }) { task ->\n"
            "                        TaskCard(task = task, onClick = { onTaskClick(task.id) })\n"
            "                    }\n"
            "                }\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "}"
        ),
        "viewmodel": (
            "package com.example.app.viewmodel\n"
            "\n"
            "import androidx.lifecycle.ViewModel\n"
            "import androidx.lifecycle.viewModelScope\n"
            "import com.example.app.data.repository.TaskRepository\n"
            "import com.example.app.model.Task\n"
            "import dagger.hilt.android.lifecycle.HiltViewModel\n"
            "import kotlinx.coroutines.flow.*\n"
            "import kotlinx.coroutines.launch\n"
            "import javax.inject.Inject\n"
            "\n"
            "data class TaskListUiState(\n"
            "    val tasks: List<Task> = emptyList(),\n"
            "    val isLoading: Boolean = false,\n"
            "    val error: String? = null,\n"
            ")\n"
            "\n"
            "@HiltViewModel\n"
            "class TaskListViewModel @Inject constructor(\n"
            "    private val repository: TaskRepository,\n"
            ") : ViewModel() {\n"
            "\n"
            "    val uiState: StateFlow<TaskListUiState> = repository\n"
            "        .observeAll()\n"
            "        .map { tasks -> TaskListUiState(tasks = tasks) }\n"
            "        .catch { e -> emit(TaskListUiState(error = e.message)) }\n"
            "        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), TaskListUiState(isLoading = true))\n"
            "\n"
            "    fun toggleComplete(task: Task) {\n"
            "        viewModelScope.launch {\n"
            "            repository.update(task.copy(isCompleted = !task.isCompleted))\n"
            "        }\n"
            "    }\n"
            "\n"
            "    fun deleteTask(id: String) {\n"
            "        viewModelScope.launch {\n"
            "            repository.delete(id)\n"
            "        }\n"
            "    }\n"
            "}"
        ),
        "model": (
            "package com.example.app.model\n"
            "\n"
            "import androidx.room.Entity\n"
            "import androidx.room.PrimaryKey\n"
            "import kotlinx.serialization.Serializable\n"
            "import java.util.UUID\n"
            "\n"
            "@Serializable\n"
            "@Entity(tableName = \"tasks\")\n"
            "data class Task(\n"
            "    @PrimaryKey\n"
            "    val id: String = UUID.randomUUID().toString(),\n"
            "    val title: String,\n"
            "    val description: String = \"\",\n"
            "    val isCompleted: Boolean = false,\n"
            "    val createdAt: Long = System.currentTimeMillis(),\n"
            ")\n"
            "\n"
            "enum class TaskFilter {\n"
            "    ALL, ACTIVE, COMPLETED;\n"
            "\n"
            "    fun apply(tasks: List<Task>): List<Task> = when (this) {\n"
            "        ALL -> tasks\n"
            "        ACTIVE -> tasks.filter { !it.isCompleted }\n"
            "        COMPLETED -> tasks.filter { it.isCompleted }\n"
            "    }\n"
            "}"
        ),
        "repository": (
            "package com.example.app.data.repository\n"
            "\n"
            "import com.example.app.data.local.TaskDao\n"
            "import com.example.app.data.remote.TaskApi\n"
            "import com.example.app.model.Task\n"
            "import kotlinx.coroutines.Dispatchers\n"
            "import kotlinx.coroutines.flow.Flow\n"
            "import kotlinx.coroutines.withContext\n"
            "import javax.inject.Inject\n"
            "import javax.inject.Singleton\n"
            "\n"
            "@Singleton\n"
            "class TaskRepository @Inject constructor(\n"
            "    private val dao: TaskDao,\n"
            "    private val api: TaskApi,\n"
            ") {\n"
            "    fun observeAll(): Flow<List<Task>> = dao.observeAll()\n"
            "\n"
            "    suspend fun refresh() = withContext(Dispatchers.IO) {\n"
            "        val remote = api.fetchAll()\n"
            "        dao.upsertAll(remote)\n"
            "    }\n"
            "\n"
            "    suspend fun update(task: Task) = withContext(Dispatchers.IO) {\n"
            "        dao.upsert(task)\n"
            "        api.update(task.id, task)\n"
            "    }\n"
            "\n"
            "    suspend fun delete(id: String) = withContext(Dispatchers.IO) {\n"
            "        dao.deleteById(id)\n"
            "        api.delete(id)\n"
            "    }\n"
            "}"
        ),
    },
    supports_hot_reload=False,
    min_sdk_version="24",
    package_manager="gradle",
)

# ---------------------------------------------------------------------------
# 5. Expo -- cross-platform (TypeScript, managed workflow)
# ---------------------------------------------------------------------------

EXPO = MobileConfig(
    name="expo",
    display_name="Expo",
    language="typescript",
    code_block_lang="tsx",
    platform="cross_platform",
    component_extension=".tsx",
    file_structure={
        "screen": "app/{name}.tsx",
        "layout": "app/_layout.tsx",
        "component": "components/{Name}.tsx",
        "hook": "hooks/use{Name}.ts",
        "service": "services/{name}Service.ts",
    },
    rules=(
        "1. Use expo-router for file-based routing. Place screens in the app/ "
        "directory following the file-system convention: app/index.tsx for home, "
        "app/[id].tsx for dynamic routes, app/_layout.tsx for shared layouts.",

        "2. Use EAS Build (eas build) for production builds and EAS Submit "
        "for store uploads. Configure build profiles in eas.json for "
        "development, preview, and production environments.",

        "3. Use expo-image instead of React Native's Image component for "
        "better caching, blur placeholders, animated transitions, and "
        "support for modern formats (WebP, AVIF).",

        "4. Use expo-secure-store for sensitive data (tokens, credentials) "
        "and expo-file-system for file operations. Never store secrets "
        "in AsyncStorage which is unencrypted.",

        "5. Use expo-notifications for push notifications with proper "
        "permission handling. Request permissions via "
        "Notifications.requestPermissionsAsync() and register the push "
        "token with your backend.",

        "6. Configure app metadata in app.json (or app.config.ts for dynamic "
        "values). Set name, slug, version, icon, splash, and platform-specific "
        "overrides under ios and android keys.",

        "7. Use Expo SDK modules (expo-camera, expo-location, expo-haptics, "
        "expo-av) instead of bare React Native community packages. Expo "
        "modules are tested against each SDK release and support OTA updates.",

        "8. Use expo-font with useFonts hook for custom typography. Load "
        "fonts in the root layout with SplashScreen.preventAutoHideAsync() "
        "and hide the splash screen after fonts are loaded.",

        "9. NEVER invent or guess import paths. Only import from packages "
        "listed in package.json and from files that exist in the project. "
        "Verify every expo-* import against the installed SDK version.",

        "10. Output ONLY valid TypeScript/TSX code. Do not include prose, "
        "explanations, or commentary inside code blocks. Every code block "
        "must be syntactically correct and ready to save to a file.",
    ),
    golden_examples={
        "screen": (
            "import { useEffect, useState } from 'react';\n"
            "import { FlatList, StyleSheet, View } from 'react-native';\n"
            "import { useLocalSearchParams, router } from 'expo-router';\n"
            "import { Image } from 'expo-image';\n"
            "import { TaskCard } from '../components/TaskCard';\n"
            "import { useTaskStore } from '../hooks/useTaskStore';\n"
            "\n"
            "export default function TaskListScreen() {\n"
            "  const { tasks, fetchTasks, loading } = useTaskStore();\n"
            "\n"
            "  useEffect(() => {\n"
            "    fetchTasks();\n"
            "  }, []);\n"
            "\n"
            "  return (\n"
            "    <View style={styles.container}>\n"
            "      <FlatList\n"
            "        data={tasks}\n"
            "        keyExtractor={(item) => item.id}\n"
            "        renderItem={({ item }) => (\n"
            "          <TaskCard\n"
            "            task={item}\n"
            "            onPress={() => router.push(`/tasks/${item.id}`)}\n"
            "          />\n"
            "        )}\n"
            "        refreshing={loading}\n"
            "        onRefresh={fetchTasks}\n"
            "        contentContainerStyle={styles.list}\n"
            "      />\n"
            "    </View>\n"
            "  );\n"
            "}\n"
            "\n"
            "const styles = StyleSheet.create({\n"
            "  container: { flex: 1, backgroundColor: '#f5f5f5' },\n"
            "  list: { padding: 16 },\n"
            "});"
        ),
        "layout": (
            "import { useEffect } from 'react';\n"
            "import { Stack } from 'expo-router';\n"
            "import { useFonts } from 'expo-font';\n"
            "import * as SplashScreen from 'expo-splash-screen';\n"
            "import { StatusBar } from 'expo-status-bar';\n"
            "\n"
            "SplashScreen.preventAutoHideAsync();\n"
            "\n"
            "export default function RootLayout() {\n"
            "  const [fontsLoaded] = useFonts({\n"
            "    'Inter-Regular': require('../assets/fonts/Inter-Regular.ttf'),\n"
            "    'Inter-Bold': require('../assets/fonts/Inter-Bold.ttf'),\n"
            "  });\n"
            "\n"
            "  useEffect(() => {\n"
            "    if (fontsLoaded) {\n"
            "      SplashScreen.hideAsync();\n"
            "    }\n"
            "  }, [fontsLoaded]);\n"
            "\n"
            "  if (!fontsLoaded) return null;\n"
            "\n"
            "  return (\n"
            "    <>\n"
            "      <StatusBar style=\"auto\" />\n"
            "      <Stack\n"
            "        screenOptions={{\n"
            "          headerStyle: { backgroundColor: '#fff' },\n"
            "          headerTintColor: '#1a1a1a',\n"
            "          headerTitleStyle: { fontFamily: 'Inter-Bold' },\n"
            "        }}\n"
            "      >\n"
            "        <Stack.Screen name=\"index\" options={{ title: 'Tasks' }} />\n"
            "        <Stack.Screen name=\"tasks/[id]\" options={{ title: 'Detail' }} />\n"
            "      </Stack>\n"
            "    </>\n"
            "  );\n"
            "}"
        ),
        "component": (
            "import { Pressable, StyleSheet, Text, View } from 'react-native';\n"
            "import { Image } from 'expo-image';\n"
            "\n"
            "interface TaskCardProps {\n"
            "  task: {\n"
            "    id: string;\n"
            "    title: string;\n"
            "    status: 'pending' | 'done';\n"
            "    imageUrl?: string;\n"
            "  };\n"
            "  onPress: () => void;\n"
            "}\n"
            "\n"
            "export function TaskCard({ task, onPress }: TaskCardProps) {\n"
            "  return (\n"
            "    <Pressable\n"
            "      style={({ pressed }) => [styles.card, pressed && styles.pressed]}\n"
            "      onPress={onPress}\n"
            "    >\n"
            "      {task.imageUrl && (\n"
            "        <Image\n"
            "          source={task.imageUrl}\n"
            "          style={styles.image}\n"
            "          placeholder=\"L6PZfSi_.AyE_3t7t7R**0o#DgR4\"\n"
            "          transition={200}\n"
            "        />\n"
            "      )}\n"
            "      <View style={styles.content}>\n"
            "        <Text style={styles.title}>{task.title}</Text>\n"
            "        <Text style={styles.status}>{task.status}</Text>\n"
            "      </View>\n"
            "    </Pressable>\n"
            "  );\n"
            "}\n"
            "\n"
            "const styles = StyleSheet.create({\n"
            "  card: { flexDirection: 'row', padding: 12, backgroundColor: '#fff',\n"
            "          borderRadius: 12, marginBottom: 8, elevation: 1 },\n"
            "  pressed: { opacity: 0.7 },\n"
            "  image: { width: 48, height: 48, borderRadius: 8 },\n"
            "  content: { flex: 1, marginLeft: 12, justifyContent: 'center' },\n"
            "  title: { fontSize: 16, fontWeight: '600', color: '#1a1a1a' },\n"
            "  status: { fontSize: 12, color: '#888', marginTop: 2 },\n"
            "});"
        ),
        "hook": (
            "import { useCallback, useEffect, useState } from 'react';\n"
            "import * as SecureStore from 'expo-secure-store';\n"
            "\n"
            "interface Task {\n"
            "  id: string;\n"
            "  title: string;\n"
            "  status: 'pending' | 'done';\n"
            "}\n"
            "\n"
            "const API_BASE = process.env.EXPO_PUBLIC_API_URL ?? 'https://api.example.com';\n"
            "\n"
            "export function useTaskStore() {\n"
            "  const [tasks, setTasks] = useState<Task[]>([]);\n"
            "  const [loading, setLoading] = useState(false);\n"
            "  const [error, setError] = useState<string | null>(null);\n"
            "\n"
            "  const fetchTasks = useCallback(async () => {\n"
            "    setLoading(true);\n"
            "    setError(null);\n"
            "    try {\n"
            "      const token = await SecureStore.getItemAsync('auth_token');\n"
            "      const res = await fetch(`${API_BASE}/tasks`, {\n"
            "        headers: { Authorization: `Bearer ${token}` },\n"
            "      });\n"
            "      if (!res.ok) throw new Error(`HTTP ${res.status}`);\n"
            "      const data: Task[] = await res.json();\n"
            "      setTasks(data);\n"
            "    } catch (err) {\n"
            "      setError((err as Error).message);\n"
            "    } finally {\n"
            "      setLoading(false);\n"
            "    }\n"
            "  }, []);\n"
            "\n"
            "  return { tasks, loading, error, fetchTasks };\n"
            "}"
        ),
    },
    supports_hot_reload=True,
    min_sdk_version="50",
    package_manager="npm",
)

# -- Register all configs -----------------------------------------------------

register_mobile(REACT_NATIVE)
register_mobile(FLUTTER)
register_mobile(SWIFT)
register_mobile(KOTLIN_COMPOSE)
register_mobile(EXPO)
