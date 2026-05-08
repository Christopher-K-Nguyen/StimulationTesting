function [stimRateSelect,quitProgram] = getStimRateParam()
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Dialog
LIST_SIZE = [280 120];

%% Variables
confirmStimulationRate = false;
quitProgram = false;

%% Function
while confirmStimulationRate == false
    fprintf('Enter stimulation rate selection...');
    
    % Channel list
%     stimRate_arr = [50,500,5e3,10e3,100,200,800,1e3,2e3,8e3];
    stimRate_arr = [50 100 200 500 1e3 2e3 5e3 10e3];
    stimRate_arr_len = length(stimRate_arr);
    stimRate_cell = cell(stimRate_arr_len,1);
    for idx = 1:stimRate_arr_len
        stimRate = stimRate_arr(idx);
        stimRate_cell{idx} = [addCommas(stimRate) ' pps'];
    end
    
    % Dialog box
    titleListStimRate = 'Stimulation Rate Selection';      % list title
    promptListStimRate = 'Select stimulation rate.';     % instruction
    [stimRate_idx,stimRate_tf] = listdlg(...	% list dialog
        'PromptString',promptListStimRate,... % list prompts
        'ListString',stimRate_cell,...    % list
        'Name',titleListStimRate,...                 % list title
        'ListSize',LIST_SIZE);
    
    % Collect input
    if stimRate_tf == false             % cancel detected
        fprintf('\nQuitting...'); % quitting
        quitProgram = true;
        return;                     % exit program
    end
    
    % Confirm stimulation rate selection
    titleQuestStimRate = 'Confirm Stimulation Rate Selection';
    % Format questions
    numOfRate = length(stimRate_idx);
    if numOfRate == 1
        stimRateSelect = stimRate_arr(stimRate_idx);
        stimRate_use = addCommas(stimRateSelect);
    else
        stimRateSelect = stimRate_arr(stimRate_idx);
        stimRate_use = '';
        for idx = stimRate_idx
            stimRate_choice = stimRate_arr(idx);
            stimRate_comma = addCommas(stimRate_choice);
            stimRate_use = append(stimRate_use,stimRate_comma,', ');
        end
        stimRate_use_len = length(stimRate_use);
        stimRate_use(stimRate_use_len-1:stimRate_use_len) = [];
    end
    stimRate_str = sprintf('Stimulation Rate: {\\bf%s pps}',stimRate_use);
    % Question box
    questListCh = questdlg(...                      % question dialog
        stimRate_str,...                       % question prompts
        titleQuestStimRate,...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questListCh                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmStimulationRate = true;               % confirm info
            fprintf('OK\n');  % info confirmed
        case BUTTON_TRY                             % try again
            confirmStimulationRate = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            return;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            return;                                 % exit program
    end
end

end