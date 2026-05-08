function quitProgram = pauseStim(pulseNum)
%% Constants
% Buttons
BUTTON_OK = 'Resume';
BUTTON_QUIT = 'Quit';
% Titles
TITLE_PAUSE = 'PAUSE';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Function
pulseNum_char = sprintf('%e',pulseNum);
pulseNum_fix = strrep(pulseNum_char,'+','');
prompt1 = sprintf('Pausing stimulation at %s',pulseNum_fix); % pause setup
fprintf(prompt1);

% Notice
prompt2 = 'Press "OK" to resume';
prompt3 = 'Check all connections!';
prompt = {prompt1,prompt2,prompt3};
notice = questdlg(prompt,TITLE_PAUSE,BUTTON_OK,BUTTON_QUIT,opts);
quitProgram = false;
switch notice
    case BUTTON_OK
        fprintf('Resuming...');
    case BUTTON_QUIT                % quit
        fprintf('Quitting...\n\n'); % quitting
        quitProgram = true;
        return;                     % exit program
    otherwise                       % cancel
        fprintf('Quitting...\n\n'); % quitting
        quitProgram = true;
        return;                     % exit program
end

fprintf('OK.\n\n');

end