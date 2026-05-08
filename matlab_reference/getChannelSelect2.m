function [listChannel,channelSelect,numOfChannel,quitProgram] = getChannelSelect2(numOfStim)
%% Constants
FIRST = 1;
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Values
NO = 0;
FALSE = 0;
ONE = 1;
MAX_CHANNEL_PER_STIM = 16;
%% Variables
confirmChannelSelect = 0;
quitProgram = 0;

%% Function
while confirmChannelSelect == NO
    fprintf('Enter channel selection...');
    
    % Channel list
    maxNumOfChannel = MAX_CHANNEL_PER_STIM * numOfStim;
    listChannel = strings(ONE,maxNumOfChannel);
    for stimNum = FIRST:numOfStim
        for channelNum = FIRST:MAX_CHANNEL_PER_STIM
            listChannel(channelNum) = sprintf('Stimulator %d Channel %02d',stimNum,channelNum);
        end
    end
    promptQuestListCh_cell = cellstr(listChannel);
    
    % Dialog box
    titleListCh = 'Channel Selection';      % list title
    promptListCh = {...                     % list prompts
        'Select channels to stimulate.',... % insrtuction
        'Hold CTRL for multi-select.'};     % multi-select
    [channelSelect,listChannel_tf] = listdlg(...	% list dialog
        'PromptString',promptListCh,... % list prompts
        'ListString',promptQuestListCh_cell,...    % list
        'Name',titleListCh);           % list title
    
    % Collect input
    numOfChannel = length(channelSelect);
    if listChannel_tf == FALSE             % cancel detected
        fprintf('\nQuitting...'); % quitting
        quitProgram = 1;
        return;                     % exit program
    end
    
    % Confirm channel selection
    titleQuestListCh = 'Confirm Channel Selection';
    % Format questions
    listChannelSelect_str = strings(numOfChannel,ONE);
    for channel_idx = channelSelect
        channelName = listChannel(channel_idx);
        listChannelSelect_str(channel_idx) = sprintf('{\\bf%s}',channelName);
    end
    prompt = 'Channels selected:';
    promptQuestListCh = cellstr([prompt;listChannelSelect_str]);
    % Question box
    questListCh = questdlg(...                      % question dialog
        promptQuestListCh,...                       % question prompts
        titleQuestListCh,...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questListCh                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmChannelSelect = 1;               % confirm info
            fprintf('OK.\n\n');  % info confirmed
        case BUTTON_TRY                             % try again
            confirmChannelSelect = 0;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = 1;
            return;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = 1;
            return;                                 % exit program
    end
end

end