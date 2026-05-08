function closeScope(File)
%% Variables
try
    scope = File.Oscilloscope.Object;      % oscilloscope
catch
    scope = File;
end

%% Function
fprintf('Closing oscilloscope...');
scopeStatus = scope.Status;
if strcmpi(scopeStatus,'open')
    fclose(scope);
    File.Oscilloscope.Object = scope;
end
fprintf('OK\n');

end