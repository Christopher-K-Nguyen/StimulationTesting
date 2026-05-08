function [File,isQuit] = getElectrodeArea(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
BUTTON_SINGLE = 'Single';
BUTTON_INDIV = 'Individually';
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmSurfaceAreaInput = false;
confirmSurfaceArea = false;
isQuit = false;

% Groups
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);
data_arr = zeros(numOfGroups,1);
surfaceArea_arr = data_arr;

%% Function
while ~confirmSurfaceAreaInput
    fprintf('Enter surface area input...');
    % Format questions
    promptQuestInput = {'Select how to input surface area:'};
    % Question box
    questInput = questdlg(...                      % question dialog
        promptQuestInput,...                       % question prompts
        'Surface Area Input',...                        % question title
        BUTTON_SINGLE,BUTTON_INDIV,BUTTON_SINGLE); % buttons

    % Format questions
    promptQuestConfirmInput = sprintf( ...
        'Select how to input surface area: \\bf{%s}', ...
        questInput);
    % Question box
    questConfirmInput = questdlg(...                      % question dialog
        promptQuestConfirmInput,...                       % question prompts
        'Surface Area Input',...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questConfirmInput                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmSurfaceAreaInput = true;               % confirm info
            surfaceAreaChoice = questInput;
            fprintf('%s\n',surfaceAreaChoice);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmSurfaceAreaInput = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
    end
end
if isQuit
    return;
end

switch surfaceAreaChoice
    case BUTTON_SINGLE
        promptSurfaceArea = {'Enter surface area (um^2):'};
        defaultSurfaceArea = {''};
    case BUTTON_INDIV
        promptSurfaceArea = cell(1,numOfGroups);
        defaultSurfaceArea = cell(numOfGroups,1);
        for groupNum = 1:numOfGroups
            channelNum = channelGroup_mat(groupNum,1);
            promptSurfaceArea{groupNum} = sprintf( ...
                'Channel %d (um^2): ',channelNum);
            defaultSurfaceArea{groupNum} = '';
        end
end
while ~confirmSurfaceArea
    fprintf('Enter surface area (um2)...');
    inputSurfaceArea = inputdlg( ...
        promptSurfaceArea, ...
        'Surface Area', ....
        [1 40],...
        defaultSurfaceArea, ...
        opts);
    if isempty(inputSurfaceArea)
        break;
    end
    defaultSurfaceArea = inputSurfaceArea;
    
    switch surfaceAreaChoice
        case BUTTON_SINGLE
            surfaceArea = str2double(inputSurfaceArea);
            surfaceArea_arr(1:numOfGroups) = surfaceArea;
            surfaceArea_use = addCommas(surfaceArea);
            inputSurfaceArea_use = sprintf('Surface area (um^2): \\bf{%s}',surfaceArea_use);
            promptQuestSurfaceArea = {inputSurfaceArea_use};
            % isAreaSame = true;
        case BUTTON_INDIV
            promptQuestSurfaceArea = cell(numOfGroups,1);
            for groupNum = 1:numOfGroups
                channelNum = channelGroup_mat(groupNum,1);
                inputSurfaceArea_char = inputSurfaceArea{groupNum};
                inputSurfaceArea_val = str2double(inputSurfaceArea_char);
                surfaceArea_arr(groupNum) = inputSurfaceArea_val;
                promptQuestSurfaceArea{groupNum} = sprintf( ...
                    '\\rm{Channel %d (um^2):} \\bf{%s}', ...
                    channelNum, ...
                    inputSurfaceArea_char);
            end
            isAreaSame = false;
    end
    
    questChoice = questdlg( ...
        promptQuestSurfaceArea, ...
        'Surface Area', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ...
        opts);
    % Confirmation
    switch questChoice                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmSurfaceArea = true;               % confirm info
            switch surfaceAreaChoice
                case BUTTON_SINGLE
                    fprintf('%g um2\n',surfaceArea);
                case BUTTON_INDIV
                    fprintf('OK\n');
            end
        case BUTTON_TRY                             % try again
            confirmSurfaceArea = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
    end
end
if isQuit
    return;
end

%% Store
File.Parameters.SurfaceArea = surfaceArea_arr;

end