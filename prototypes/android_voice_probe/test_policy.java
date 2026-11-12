import java.nio.file.*;
import java.util.*;
import javax.tools.ToolProvider;

/** Exercise the exact policy embedded in MainActivity.java, without Android or JUnit. */
class TestPolicy {
    public static void main(String[] args) throws Exception {
        Path root = Path.of(System.getProperty("user.dir"));
        String source = Files.readString(root.resolve("app/src/main/java/as/adamsmith/dogwalk/audioprobe/MainActivity.java"));
        int marker = source.indexOf("final class ProbePolicy");
        if (marker < 0) throw new AssertionError("MainActivity.java no longer contains ProbePolicy");
        String policy = "import java.util.Locale;\n" + source.substring(marker);
        Path output = root.resolve("app/build/policy-test");
        Files.createDirectories(output);
        Path input = output.resolve("ProbePolicy.java");
        Files.writeString(input, policy);
        int result = ToolProvider.getSystemJavaCompiler().run(null, null, null, "-d", output.toString(), input.toString());
        if (result != 0) throw new AssertionError("Policy did not compile");
        try (var loader = new java.net.URLClassLoader(new java.net.URL[]{output.toUri().toURL()})) {
            Class<?> type = loader.loadClass("ProbePolicy");
            var reply = type.getDeclaredMethod("reply", String.class);
            reply.setAccessible(true);
            int count = 0;
            for (String[] test : new String[][]{
                {"Stop listening.", "true", ""}, {" STOP   THE PROBE! ", "true", ""},
                {"please stop listening tomorrow", "false", "I heard:"},
                {"long reply", "false", "This is a deliberately long"},
                {"long test reply", "false", "This is a deliberately long"},
                {"probe status", "false", "The local audio probe"},
                {"check the build", "false", "I heard: check the build"},
                {"do not stop listening", "false", "I heard:"},
                {"stop listening now", "false", "I heard:"},
                {"", "false", "I heard: "},
            }) {
                Object value = reply.invoke(null, test[0]);
                var stop = value.getClass().getDeclaredMethod("stop"); stop.setAccessible(true);
                var text = value.getClass().getDeclaredMethod("text"); text.setAccessible(true);
                String actualText = text.invoke(value).toString();
                if (!stop.invoke(value).toString().equals(test[1])
                        || !(test[1].equals("true") ? actualText.equals(test[2]) : actualText.startsWith(test[2])))
                    throw new AssertionError(Arrays.toString(test));
                count++;
            }
            System.out.println(count + " policy tests passed");
        }
    }
}
