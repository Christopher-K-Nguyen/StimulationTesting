function [File,quitProgram] = getIndexInfo_Tek(File)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
name = '';
surfaceArea_raw = '';
surfaceArea = [];
confirmInfo = false;
quitProgram = false;

%% Function
while ~confirmInfo
    inputName = 'Name: ';
    if captureNum > 1      
        fprintf('Name List:\n');
        name_cell_raw = {File.Data.Name};
        name_cell_idx = cellfun(@ischar,name_cell_raw);
        name_cell = name_cell_raw(name_cell_idx);
        name = name_cell{captureNum-1};
        surfaceArea = File.Data(captureNum-1).SurfaceArea;
        surfaceArea_raw = num2str(surfaceArea);
        fprintf('\t%s\n',name_cell{:});
    else
        name_cell = '';
    end
    promptInfo = { ...
        inputName, ...
        'Surface Area (\mum^2)'};
    defaultInfo = {name,surfaceArea_raw};
    inputInfo = inputdlg( ...
        promptInfo, ...
        'Capture Information', ...
        [1 60], ...
        defaultInfo, ...
        opts);
    if isempty(inputInfo)
        break;
    end
    name = inputInfo{1};
    surfaceArea_raw = inputInfo{2};
    surfaceArea_fix = erase(surfaceArea_raw,{',',' ','''',});
    surfaceArea = str2double(surfaceArea_fix);
    name_fix = strrep(name,'_','\_');
    if any(strcmp(name,name_cell))
        name_use = sprintf('{\\bfALREADY EXISTS: }Name: {\\bf%s}',name_fix);
    else
        name_use = sprintf('Name: {\\bf%s}',name_fix);
    end
    surfaceArea_comma = addCommas(surfaceArea);
    surfaceArea_use = sprintf('Surface Area (\\mum^{2}): {\\bf%s}',surfaceArea_comma);
    questPrompt = {name_use,surfaceArea_use};
    opts.Default = BUTTON_CONFIRM;
    questInfo = questdlg( ...
        questPrompt, ...
        'Confirm Information', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
        opts);

    switch questInfo
        case BUTTON_CONFIRM
            fprintf('Name: %s\n',name);
            fprintf('Surface Area (um2): %s\n',surfaceArea_comma);
            confirmInfo = true;
        case BUTTON_TRY
            continue;
        case BUTTON_QUIT
            quitProgram = false;
            break;
    end
end
File.Data(captureNum).Name = name;
File.Data(captureNum).SurfaceArea = surfaceArea;

end